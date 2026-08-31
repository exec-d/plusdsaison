"""Client Copernicus Climate Data Store.

Le dataset `derived-era5-land-daily-statistics` fournit directement les
minima, maxima et moyennes quotidiens : inutile de télécharger l'horaire pour
l'agréger, ce qui divise le volume par 24.

Les requêtes passent par une file d'attente qui peut tenir des heures. Le
découpage est donc annuel et le cache disque rend l'exécution reprenable :
une année déjà téléchargée n'est jamais redemandée.
"""

from pathlib import Path

import cdsapi

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


def _telecharger(client, dataset: str, requete: dict, cible: Path) -> Path:
    """Télécharge sous un nom provisoire, puis renomme.

    `cdsapi` écrit ses morceaux directement dans la cible, et le cache ne
    regarde que la taille : une coupure y laisse un fichier partiel que la
    relance resservirait tel quel. Mesuré sur un fichier tronqué à 30 Mo sur
    54 : `xarray` le refuse à l'ouverture (« NetCDF: HDF error »), donc rien
    de faux n'est publié — mais l'assemblage s'arrête après des heures de
    téléchargement, en nommant un fichier qu'il faut aller supprimer soi-même.
    Le renommage rend la reprise automatique.
    """
    partiel = cible.with_name(cible.name + ".partiel")
    partiel.unlink(missing_ok=True)
    try:
        client.retrieve(dataset, requete, str(partiel))
        partiel.replace(cible)
    finally:
        partiel.unlink(missing_ok=True)
    return cible


def retrieve_year(
    client, variable: str, statistic: str, year: int, cache_dir: Path
) -> Path:
    """Télécharge une année, ou rend le fichier déjà présent en cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cible = cache_dir / f"{variable}_{statistic}_{year}.nc"

    if cible.exists() and cible.stat().st_size > 0:
        return cible

    return _telecharger(client, DATASET, build_request(variable, statistic, year), cible)


#: Les heures d'une journée complète, pour le dataset horaire.
_HOURS = [f"{h:02d}:00" for h in range(24)]

#: Les mois de chaque trimestre.
#:
#: Le découpage n'est pas esthétique, il est imposé : une année entière
#: d'horaire dépasse la limite de coût de Copernicus (« your request is too
#: large »), un trimestre passe. Douze requêtes mensuelles passeraient aussi,
#: mais coûteraient trois fois plus d'allers-retours pour le même volume.
QUARTERS = {
    1: ["01", "02", "03"],
    2: ["04", "05", "06"],
    3: ["07", "08", "09"],
    4: ["10", "11", "12"],
}


def build_hourly_temperature_request(year: int, quarter: int) -> dict:
    """Un trimestre de température horaire, sur le dataset rapide.

    **Pourquoi l'horaire plutôt que les statistiques quotidiennes déjà
    calculées.** `derived-era5-land-daily-statistics` livre directement Tmin,
    Tmax et Tmoy, ce qui divise le volume par 24 — mais sa file d'attente met
    6 à 16 heures par requête et n'en sert qu'une à la fois. Mesuré sur le
    compte : cinq requêtes soumises ensemble à 11 h 55 sont revenues à 18 h 27,
    20 h 36, 23 h 02 et 3 h 39, la cinquième en échec. À trois requêtes par
    année, reconstruire 1950-2025 y prend des semaines.

    `reanalysis-era5-land` sert la même donnée en quelques minutes — un
    trimestre mesuré à 4 min 30 pour 52 Mo. On paie en volume (~16 Go pour
    1950-2025 au lieu d'environ 1) ce qu'on gagne en semaines d'attente.

    L'agrégation locale reproduit le dataset dérivé : vérifié sur le premier
    trimestre 2024, les 90 journées complètes sont identiques à 0,001 K près.
    """
    return {
        "variable": ["2m_temperature"],
        "year": str(year),
        "month": QUARTERS[quarter],
        "day": _DAYS,
        "time": _HOURS,
        "area": AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def retrieve_hourly_temperature(
    client, year: int, quarter: int, cache_dir: Path
) -> Path:
    """Télécharge un trimestre horaire, ou rend celui déjà en cache."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cible = cache_dir / f"2m_temperature_hourly_{year}_T{quarter}.nc"

    if cible.exists() and cible.stat().st_size > 0:
        return cible

    return _telecharger(
        client, STATIC_DATASET, build_hourly_temperature_request(year, quarter), cible
    )


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

    return _telecharger(client, STATIC_DATASET, build_static_request(), cible)


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

    return _telecharger(client, DATASET, build_land_probe_request(), cible)


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

    return _telecharger(
        client, PRECIPITATION_DATASET, build_precipitation_request(year), cible
    )


# Rejeux d'une erreur HTTP avant d'abandonner, et attente entre deux.
#
# `cdsapi` en tente 500 à deux minutes d'intervalle, soit seize heures
# pendant lesquelles le journal n'écrit que « Recovering from HTTP error ».
# Ce n'est pas une hypothèse : un secret `CDSAPI_KEY` vide fait répondre 500
# au CDS — et non 401, qui aurait interrompu tout de suite —, et le
# rafraîchissement quotidien s'est fait tuer à la limite des six heures de
# GitHub vingt-quatre jours d'affilée sans jamais rien télécharger.
#
# Dix minutes absorbent une panne passagère ; au-delà, la panne est
# installée et attendre ne la résout pas.
RETRY_MAX = 5
SLEEP_MAX_S = 120


def make_client(**extra):
    """Client CDS dont les rejeux sont bornés à dix minutes.

    Passer par ici plutôt que par `cdsapi.Client()` : c'est le seul endroit
    où la borne est écrite.

    **L'attente en file n'est pas raccourcie.** `retry_max` ne borne que deux
    boucles — les rejeux d'une erreur HTTP, qui dorment exactement `sleep_max`
    entre deux, et les reprises d'un téléchargement interrompu. Le sondage
    d'un travail en attente est une boucle distincte, sans compteur, dont
    `sleep_max` ne plafonne que l'intervalle. Un backfill peut donc toujours
    passer des heures en file. Vérifié dans `cdsapi/api.py` 0.7.4.
    """
    reglages = {"retry_max": RETRY_MAX, "sleep_max": SLEEP_MAX_S}
    reglages.update(extra)
    return cdsapi.Client(**reglages)
