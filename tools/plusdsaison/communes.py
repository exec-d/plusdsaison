"""Référentiel des communes françaises et de leur altitude.

Les communes viennent de geo.api.gouv.fr, les altitudes de l'API Elevation
d'Open-Meteo. Cette dernière accepte des requêtes groupées : 100 points par
appel ramènent les 35 000 communes en 350 requêtes, exécutées une seule fois
à la construction de l'index.
"""

from dataclasses import dataclass

GEO_API = "https://geo.api.gouv.fr/communes"
ELEVATION_API = "https://api.open-meteo.com/v1/elevation"
ELEVATION_BATCH = 100


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
