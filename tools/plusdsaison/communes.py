"""Référentiel des communes françaises et de leur altitude.

Les communes viennent de geo.api.gouv.fr, les altitudes du service
altimétrique de la Géoplateforme IGN, qui expose le RGE ALTI.

Open-Meteo, initialement prévu, ne convient pas : son offre gratuite compte
un appel *par point demandé* et non par requête. Les 35 000 communes pèsent
donc 35 000 unités contre 10 000 autorisées par jour — aucun rythme ne les
fait passer, et les essais se terminent invariablement en HTTP 429 puis 503.
L'IGN n'a pas cette limite, accepte 150 points par requête, et donne pour la
France des altitudes plus justes : 278,5 m à Villars-les-Dombes contre 279,0
et 1 035,7 m à Chamonix contre 1 041,0.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests
from scipy.spatial import cKDTree

from .grid import cell_id as _cell_id

GEO_API = "https://geo.api.gouv.fr/communes"
ELEVATION_API = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
ELEVATION_RESOURCE = "ign_rge_alti_wld"
ELEVATION_BATCH = 150

# Valeur que rend l'IGN pour un point hors de sa couverture : en mer, et sur
# quelques territoires ultramarins. À ne surtout pas confondre avec une
# altitude, sous peine de placer des communes à 99 km sous le niveau de la mer.
ELEVATION_NO_DATA = -99999.0

# En bordure de couverture le service interpole entre du relief réel et sa
# sentinelle, ce qui produit des valeurs intermédiaires qu'aucun test
# d'égalité n'attrape : six communes insulaires et littorales sont ainsi
# ressorties entre -52 000 et -87 000 m. Une altitude de -87 000 m
# appliquerait une correction de +566 °C à la commune concernée, sans que
# rien ne le signale. Seules les valeurs physiquement plausibles sont donc
# retenues.
#
# La borne basse est serrée à dessein : le point le plus bas de France est à
# environ -4 m, dans les polders du Nord et le delta du Rhône. Une borne à
# -500 m laissait encore passer Talmont-sur-Gironde à -269 m — un village
# perché sur une falaise de l'estuaire, dont la mairie tombe en bordure de
# couverture. 50 m de marge suffisent largement et rattrapent ce cas.
ELEVATION_MIN_M = -50.0
ELEVATION_MAX_M = 5000.0

# Le service reste courtois mais n'est pas sans limite : une courte pause
# entre deux lots, et une reprise exponentielle en cas de refus.
ELEVATION_PAUSE_S = 0.2
ELEVATION_RETRIES = 6
ELEVATION_BACKOFF_MAX_S = 30.0

# Le service ne borne pas le nombre de points mais le nombre de dalles
# raster qu'il doit ouvrir pour y répondre. Un lot de communes voisines
# passe sans problème ; le même nombre de points éparpillés de la Martinique
# à la Nouvelle-Calédonie se fait refuser. Comme geo.api.gouv.fr rend les
# communes groupées par département, seul le paquet ultramarin final est
# concerné : on le scinde jusqu'à ce qu'il passe.
ELEVATION_TOO_WIDE = "ROK4_TOO_MUCH_TILES"

EARTH_RADIUS_KM = 6371.0
MAX_DISTANCE_KM = 15.0


@dataclass
class Commune:
    insee: str
    nom: str
    departement: str
    lat: float
    lon: float
    altitude: float | None = None
    cell_id: int | None = None
    distance_km: float | None = None
    #: Les codes postaux de la commune, dans l'ordre rendu par geo.api.gouv.fr.
    #:
    #: Une liste, parce que la relation n'est bijective ni dans un sens ni dans
    #: l'autre : Paris en compte vingt, et 01330 en couvre six à lui seul. Elle
    #: peut être vide — quelques communes n'en ont aucun de publié.
    codes_postaux: list[str] = field(default_factory=list)


def fetch_communes(session) -> list[Commune]:
    """Toutes les communes de France métropolitaine et d'outre-mer.

    La position retenue est celle de la **mairie**, pas le centroïde de la
    commune. L'altitude qui en découle sert à corriger la température pour
    les habitants : c'est donc celle du bourg qu'il faut, pas celle du centre
    géométrique d'un polygone. Chamonix-Mont-Blanc le montre bien — sa
    commune englobe le mont Blanc, son centroïde tombe à 1 885 m à mi-pente
    quand la ville est à 1 035 m. Les 850 m d'écart valent 5,5 °C de
    correction adiabatique.

    Les codes postaux sont demandés dans la même requête : c'est par eux que
    les gens cherchent leur commune. Personne ne connaît son code INSEE, et une
    recherche qui l'exigerait est une recherche que l'on croit vide.
    """
    reponse = session.get(
        GEO_API,
        params={
            "fields": "nom,code,codeDepartement,centre,mairie,codesPostaux",
            "format": "json",
        },
        timeout=120,
    )
    reponse.raise_for_status()

    communes = []
    for brut in reponse.json():
        # Quelques communes n'ont pas de mairie géolocalisée ; le centroïde
        # prend alors le relais, faute de mieux.
        point = brut.get("mairie") or brut.get("centre")
        if not point:
            # Quelques entités administratives n'ont aucune coordonnée
            # publiée : sans elles, aucun rattachement n'est possible.
            continue
        lon, lat = point["coordinates"]
        communes.append(
            Commune(
                insee=brut["code"],
                nom=brut["nom"],
                departement=brut["codeDepartement"],
                lat=float(lat),
                lon=float(lon),
                # Filtrés sur cinq chiffres : l'API rend parfois une chaîne
                # vide, et un code non conforme casserait un format qui
                # réserve exactement cinq octets par entrée.
                codes_postaux=[
                    code
                    for code in brut.get("codesPostaux") or []
                    if len(code) == 5 and code.isdigit()
                ],
            )
        )
    return communes


def _get_avec_reprise(session, params: dict, pause) -> object:
    """Un appel Elevation, en réessayant tant qu'on est limité en débit."""
    for tentative in range(ELEVATION_RETRIES):
        reponse = session.get(ELEVATION_API, params=params, timeout=120)
        # Les fausses sessions des tests ne portent pas de code HTTP :
        # les traiter comme des succès.
        if getattr(reponse, "status_code", 200) not in (429, 503):
            reponse.raise_for_status()
            return reponse
        pause(min(2.0 * 2**tentative, ELEVATION_BACKOFF_MAX_S))
    raise RuntimeError(
        f"le service altimétrique refuse toujours après {ELEVATION_RETRIES} "
        "tentatives ; réduire ELEVATION_BATCH ou reprendre plus tard"
    )


def _lot_trop_etendu(erreur: Exception) -> bool:
    """Le service a-t-il refusé le lot pour son étendue géographique ?"""
    reponse = getattr(erreur, "response", None)
    if reponse is None or getattr(reponse, "status_code", None) != 400:
        return False
    return ELEVATION_TOO_WIDE in getattr(reponse, "text", "")


def _relever_lot(session, lot: list[Commune], pause) -> list[float]:
    """Altitudes d'un lot, en le scindant si le service refuse son étendue."""
    try:
        reponse = _get_avec_reprise(
            session,
            {
                # L'IGN sépare ses coordonnées par des barres verticales, et
                # les attend dans l'ordre longitude puis latitude.
                "lon": "|".join(f"{c.lon:.6f}" for c in lot),
                "lat": "|".join(f"{c.lat:.6f}" for c in lot),
                "resource": ELEVATION_RESOURCE,
                "zonly": "true",
            },
            pause,
        )
    except requests.HTTPError as erreur:
        if len(lot) > 1 and _lot_trop_etendu(erreur):
            milieu = len(lot) // 2
            return (
                _relever_lot(session, lot[:milieu], pause)
                + _relever_lot(session, lot[milieu:], pause)
            )
        raise
    return reponse.json()["elevations"]


def fetch_elevations(
    session,
    communes: list[Commune],
    batch: int = ELEVATION_BATCH,
    pause=time.sleep,
    progres=None,
    cache: Path | None = None,
) -> None:
    """Renseigne `altitude` sur place, par lots groupés.

    `progres` reçoit (communes traitées, total) après chaque lot : la boucle
    dure plusieurs minutes, pendant lesquelles un appelant muet est
    indiscernable d'un appelant bloqué.

    `cache` désigne un JSON où les altitudes déjà obtenues sont conservées,
    ce qui rend l'opération reprenable : un incident réseau à la 200ᵉ requête
    ne fait pas recommencer les 199 premières.

    Les points hors couverture restent à `None` plutôt que de recevoir la
    sentinelle du service : c'est à l'appelant de décider quoi en faire.
    """
    connues: dict[str, float] = {}
    if cache is not None and Path(cache).exists():
        connues = {
            insee: float(z)
            for insee, z in json.loads(Path(cache).read_text()).items()
        }
        for commune in communes:
            if commune.insee in connues:
                commune.altitude = connues[commune.insee]

    restantes = [c for c in communes if c.altitude is None]
    traitees = len(communes) - len(restantes)

    for debut in range(0, len(restantes), batch):
        lot = restantes[debut : debut + batch]
        if debut:
            pause(ELEVATION_PAUSE_S)
        altitudes = _relever_lot(session, lot, pause)

        if len(altitudes) != len(lot):
            raise ValueError(
                f"{len(altitudes)} altitudes reçues pour {len(lot)} communes demandées"
            )
        for commune, altitude in zip(lot, altitudes):
            valeur = float(altitude)
            if not ELEVATION_MIN_M <= valeur <= ELEVATION_MAX_M:
                continue
            commune.altitude = valeur
            connues[commune.insee] = valeur

        traitees += len(lot)
        if cache is not None:
            Path(cache).write_text(json.dumps(connues))
        if progres is not None:
            progres(min(traitees, len(communes)), len(communes))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique entre deux points, en kilomètres."""
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = phi2 - phi1
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2) ** 2
    return float(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a)))


def attach_to_land_cells(
    communes: list[Commune], lats: np.ndarray, lons: np.ndarray, mask: np.ndarray
) -> None:
    """Rattache chaque commune à la maille TERRESTRE la plus proche.

    ERA5-Land ne porte pas de données en mer : un rattachement au plus proche
    voisin sans filtrage enverrait les communes littorales sur des mailles
    vides. On ne cherche donc que parmi les mailles terrestres.

    La recherche passe par un arbre k-d sur des coordonnées projetées en
    équirectangulaire — l'approximation est négligeable à l'échelle de la
    France — puis la distance retenue est recalculée en haversine exacte.
    """
    lignes, colonnes = np.nonzero(mask)
    if len(lignes) == 0:
        raise ValueError("aucune maille terrestre dans le masque fourni")

    maille_lats = lats[lignes]
    maille_lons = lons[colonnes]

    # Projection équirectangulaire centrée sur l'emprise : la longitude est
    # comprimée par le cosinus de la latitude moyenne pour que les distances
    # euclidiennes de l'arbre restent proportionnelles aux distances réelles.
    lat_ref = np.radians(float(np.mean(lats)))

    def projeter(la, lo):
        return np.column_stack([np.asarray(la), np.asarray(lo) * np.cos(lat_ref)])

    arbre = cKDTree(projeter(maille_lats, maille_lons))
    requete = projeter(
        [c.lat for c in communes],
        [c.lon for c in communes],
    )
    _, indices = arbre.query(requete, k=1)

    n_lon = len(lons)
    for commune, index in zip(communes, np.atleast_1d(indices)):
        ligne, colonne = int(lignes[index]), int(colonnes[index])
        commune.cell_id = _cell_id(ligne, colonne, n_lon)
        commune.distance_km = haversine_km(
            commune.lat, commune.lon, float(lats[ligne]), float(lons[colonne])
        )
