"""Référentiel des communes françaises et de leur altitude.

Les communes viennent de geo.api.gouv.fr, les altitudes de l'API Elevation
d'Open-Meteo. Cette dernière accepte des requêtes groupées : 100 points par
appel ramènent les 35 000 communes en 350 requêtes, exécutées une seule fois
à la construction de l'index.
"""

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .grid import cell_id as _cell_id

GEO_API = "https://geo.api.gouv.fr/communes"
ELEVATION_API = "https://api.open-meteo.com/v1/elevation"
ELEVATION_BATCH = 100

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


def fetch_elevations(session, communes: list[Commune], batch: int = ELEVATION_BATCH) -> None:
    """Renseigne `altitude` sur place, par lots groupés."""
    for debut in range(0, len(communes), batch):
        lot = communes[debut : debut + batch]
        reponse = session.get(
            ELEVATION_API,
            params={
                "latitude": ",".join(f"{c.lat:.4f}" for c in lot),
                "longitude": ",".join(f"{c.lon:.4f}" for c in lot),
            },
            timeout=60,
        )
        reponse.raise_for_status()
        altitudes = reponse.json()["elevation"]

        if len(altitudes) != len(lot):
            raise ValueError(
                f"{len(altitudes)} altitudes reçues pour {len(lot)} communes demandées"
            )
        for commune, altitude in zip(lot, altitudes):
            commune.altitude = float(altitude)


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
