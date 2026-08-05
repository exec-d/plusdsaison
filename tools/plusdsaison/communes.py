"""Référentiel des communes françaises et de leur altitude.

Les communes viennent de geo.api.gouv.fr, les altitudes de l'API Elevation
d'Open-Meteo. Cette dernière accepte des requêtes groupées : 100 points par
appel ramènent les 35 000 communes en 350 requêtes, exécutées une seule fois
à la construction de l'index.
"""

import time
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .grid import cell_id as _cell_id

GEO_API = "https://geo.api.gouv.fr/communes"
ELEVATION_API = "https://api.open-meteo.com/v1/elevation"
ELEVATION_BATCH = 100

# Open-Meteo limite le débit de son offre gratuite, et pondère ses appels
# par le nombre de points demandés : 350 lots de 100 communes ne pèsent pas
# 350 unités mais 35 000. Enchaîner les lots sans pause déclenche un HTTP 429
# au bout de quelques dizaines d'appels, et 63 s de reprise n'y suffisent
# pas. La fenêtre se recharge en revanche en quelques minutes, d'où ce
# rythme volontairement lent : la construction de l'index est ponctuelle.
ELEVATION_PAUSE_S = 1.5
ELEVATION_RETRIES = 8
ELEVATION_BACKOFF_MAX_S = 60.0

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


def fetch_communes(session) -> list[Commune]:
    """Toutes les communes de France métropolitaine et d'outre-mer."""
    reponse = session.get(
        GEO_API,
        params={"fields": "nom,code,codeDepartement,centre", "format": "json"},
        timeout=120,
    )
    reponse.raise_for_status()

    communes = []
    for brut in reponse.json():
        centre = brut.get("centre")
        if not centre:
            # Quelques entités administratives n'ont pas de centroïde publié :
            # sans coordonnées, aucun rattachement n'est possible.
            continue
        lon, lat = centre["coordinates"]
        communes.append(
            Commune(
                insee=brut["code"],
                nom=brut["nom"],
                departement=brut["codeDepartement"],
                lat=float(lat),
                lon=float(lon),
            )
        )
    return communes


def _get_avec_reprise(session, params: dict, pause) -> object:
    """Un appel Elevation, en réessayant tant qu'on est limité en débit."""
    for tentative in range(ELEVATION_RETRIES):
        reponse = session.get(ELEVATION_API, params=params, timeout=60)
        # Les fausses sessions des tests ne portent pas de code HTTP :
        # les traiter comme des succès.
        if getattr(reponse, "status_code", 200) != 429:
            reponse.raise_for_status()
            return reponse
        # Attente plafonnée : au-delà d'une minute, insister plus longtemps
        # ne sert à rien, la fenêtre s'est rechargée ou le quota est épuisé.
        pause(min(5.0 * 2**tentative, ELEVATION_BACKOFF_MAX_S))
    raise RuntimeError(
        f"Open-Meteo limite toujours le débit après {ELEVATION_RETRIES} tentatives ; "
        "réduire ELEVATION_BATCH ou reprendre plus tard"
    )


def fetch_elevations(
    session,
    communes: list[Commune],
    batch: int = ELEVATION_BATCH,
    pause=time.sleep,
    progres=None,
) -> None:
    """Renseigne `altitude` sur place, par lots groupés.

    `progres` reçoit (communes traitées, total) après chaque lot : au rythme
    imposé par Open-Meteo la boucle dure une dizaine de minutes, pendant
    lesquelles un appelant muet est indiscernable d'un appelant bloqué.
    """
    for debut in range(0, len(communes), batch):
        lot = communes[debut : debut + batch]
        if debut:
            pause(ELEVATION_PAUSE_S)
        reponse = _get_avec_reprise(
            session,
            {
                "latitude": ",".join(f"{c.lat:.4f}" for c in lot),
                "longitude": ",".join(f"{c.lon:.4f}" for c in lot),
            },
            pause,
        )
        altitudes = reponse.json()["elevation"]

        if len(altitudes) != len(lot):
            raise ValueError(
                f"{len(altitudes)} altitudes reçues pour {len(lot)} communes demandées"
            )
        for commune, altitude in zip(lot, altitudes):
            commune.altitude = float(altitude)

        if progres is not None:
            progres(min(debut + batch, len(communes)), len(communes))


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
