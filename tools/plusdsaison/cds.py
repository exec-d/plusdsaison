"""Client Copernicus Climate Data Store.

Le dataset `derived-era5-land-daily-statistics` fournit directement les
minima, maxima et moyennes quotidiens : inutile de télécharger l'horaire pour
l'agréger, ce qui divise le volume par 24.

Les requêtes passent par une file d'attente qui peut tenir des heures. Le
découpage est donc annuel et le cache disque rend l'exécution reprenable :
une année déjà téléchargée n'est jamais redemandée.
"""

from pathlib import Path

from .grid import LAT_MAX, LAT_MIN, LON_MAX, LON_MIN

DATASET = "derived-era5-land-daily-statistics"

# Le dataset des statistiques quotidiennes n'expose que des variables
# météorologiques : ni géopotentiel, ni masque terre-mer. L'orographie du
# modèle vient donc du dataset horaire, qui partage exactement la même
# grille 0,1° et dont une seule heure suffit — ces champs sont statiques.
STATIC_DATASET = "reanalysis-era5-land"

# Copernicus attend l'emprise dans l'ordre [Nord, Ouest, Sud, Est].
AREA = [LAT_MAX, LON_MIN, LAT_MIN, LON_MAX]

# Les journées sont découpées en heure légale française plutôt qu'en UTC :
# sinon les minima nocturnes glissent d'une heure et le comptage des jours
# de gel s'en trouve faussé.
TIME_ZONE = "utc+01:00"

STATISTICS = {
    "min": "daily_minimum",
    "max": "daily_maximum",
    "mean": "daily_mean",
}

# Les précipitations ERA5-Land sont livrées en mètres.
#
# Facteur vérifié empiriquement, et non déduit de la documentation : sur la
# maille de la Dombes (46,0 N / 5,0 E), le cumul de 2023 vaut 1 104 mm avec
# ce facteur, contre 26 501 mm si l'on y ajoutait un ×24. Se tromper d'un
# facteur 24 sur toute la pluie de France passerait tous les tests unitaires
# sans être détecté. Ne pas modifier sans refaire cette mesure.
PRECIPITATION_FACTOR = 1000.0

# `total_precipitation` est une grandeur accumulée, que le dataset des
# statistiques quotidiennes refuse explicitement :
#
#   « Daily statistics of accumulated variables are not supported for this
#     dataset, skipping: total_precipitation. »
#
# Elle vient donc du dataset horaire. Une seule heure par jour suffit :
# l'accumulation d'une journée se lit à 00:00 du lendemain.
PRECIPITATION_DATASET = STATIC_DATASET

# Décalage entre l'horodatage lu et la journée qu'il décrit : la valeur
# datée du 2 janvier à 00:00 est le cumul du 1er janvier.
PRECIPITATION_DAY_SHIFT = -1

_MONTHS = [f"{m:02d}" for m in range(1, 13)]
_DAYS = [f"{d:02d}" for d in range(1, 32)]


def build_request(variable: str, statistic: str, year: int) -> dict:
    """Requête CDS couvrant une année entière sur l'emprise France.

    `download_format: unarchived` n'est pas décoratif : sans lui Copernicus est
    libre de livrer un zip, que `xarray` ne sait pas ouvrir. Les deux autres
    constructeurs de requête de ce fichier le fixent déjà — celui-ci l'omettait,
    et c'est le seul des trois dont l'échec coûte cher : une année complète
    passe des heures en file d'attente avant qu'on découvre le format du
    fichier reçu.
    """
    return {
        "variable": [variable],
        "year": str(year),
        "month": _MONTHS,
        "day": _DAYS,
        "daily_statistic": statistic,
        "time_zone": TIME_ZONE,
        "frequency": "1_hourly",
        "area": AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def retrieve_year(
    client, variable: str, statistic: str, year: int, cache_dir: Path
) -> Path:
    """Télécharge une année, ou rend le fichier déjà présent en cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cible = cache_dir / f"{variable}_{statistic}_{year}.nc"

    if cible.exists() and cible.stat().st_size > 0:
        return cible

    client.retrieve(DATASET, build_request(variable, statistic, year), str(cible))
    return cible


def build_static_request() -> dict:
    """Requête d'une heure sur le dataset horaire, pour l'orographie.

    Le géopotentiel de surface ne varie pas dans le temps : n'importe quelle
    heure convient, et une seule suffit.

    Une seule variable est demandée à dessein. Copernicus livre une archive
    zip dès qu'une requête NetCDF en porte plusieurs, même avec
    `download_format: unarchived` — et `xarray` ne sait pas ouvrir un zip.
    Le masque terre s'obtient de toute façon sans `land_sea_mask` : ERA5-Land
    ne modélise pas la mer, donc le géopotentiel y est absent.
    """
    return {
        "variable": ["geopotential"],
        "year": "2020",
        "month": "01",
        "day": "01",
        "time": ["00:00"],
        "area": AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def retrieve_static(client, cache_dir: Path) -> Path:
    """Télécharge l'échantillon statique, ou rend celui déjà en cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cible = cache_dir / "era5_land_static.nc"

    if cible.exists() and cible.stat().st_size > 0:
        return cible

    client.retrieve(STATIC_DATASET, build_static_request(), str(cible))
    return cible


def build_land_probe_request() -> dict:
    """Requête d'une seule journée de température sur toute l'emprise.

    Sert uniquement à relever quelles mailles sont effectivement servies.
    """
    requete = build_request("2m_temperature", STATISTICS["mean"], 2023)
    requete["month"] = ["07"]
    requete["day"] = ["15"]
    return requete


def retrieve_land_probe(client, cache_dir: Path) -> Path:
    """Télécharge la sonde du masque terre, ou rend celle déjà en cache.

    Le masque terre ne se déduit ni du géopotentiel ni de `land_sea_mask` :
    le premier est fini partout, y compris en mer, et le second désigne
    11 493 mailles là où ERA5-Land en sert 11 496 — 143 mailles en
    désaccord. Seule la disponibilité réelle d'une variable
    météorologique donne le masque exact, et c'est celui-là qu'il faut :
    publier une maille sans données produirait un fichier vide.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cible = cache_dir / "land_probe.nc"

    if cible.exists() and cible.stat().st_size > 0:
        return cible

    client.retrieve(DATASET, build_land_probe_request(), str(cible))
    return cible


def build_precipitation_request(year: int) -> dict:
    """Requête d'une année de précipitations, une seule heure par jour.

    Demander 00:00 tous les jours de l'année Y ramène les cumuls des
    journées du 31 décembre Y-1 au 30 décembre Y : c'est le décalage que
    `PRECIPITATION_DAY_SHIFT` corrige à l'assemblage.
    """
    return {
        "variable": ["total_precipitation"],
        "year": str(year),
        "month": _MONTHS,
        "day": _DAYS,
        "time": ["00:00"],
        "area": AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def retrieve_precipitation_year(client, year: int, cache_dir: Path) -> Path:
    """Télécharge une année de précipitations, ou rend le fichier en cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cible = cache_dir / f"total_precipitation_{year}.nc"

    if cible.exists() and cible.stat().st_size > 0:
        return cible

    client.retrieve(PRECIPITATION_DATASET, build_precipitation_request(year), str(cible))
    return cible
