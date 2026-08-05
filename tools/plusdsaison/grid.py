"""Grille ERA5-Land restreinte à la France métropolitaine.

ERA5-Land publie sur une grille régulière de 0,1° (~9 km). On en extrait
l'emprise française, et chaque maille reçoit un identifiant stable calculé
depuis sa position dans cette emprise. Cet identifiant est publié dans les
fichiers et référencé par l'index : sa formule ne peut pas changer sans
invalider toutes les données déjà distribuées.
"""

import numpy as np

STEP = 0.1
LAT_MAX = 51.5
LAT_MIN = 41.0
LON_MIN = -5.5
LON_MAX = 10.0


def grid_axes() -> tuple[np.ndarray, np.ndarray]:
    """Latitudes (décroissantes, convention ERA5) et longitudes (croissantes).

    L'arrondi au dixième est indispensable : `arange` accumule une erreur
    flottante qui empêcherait sinon d'apparier ces axes à ceux du NetCDF.
    """
    lats = np.round(np.arange(LAT_MAX, LAT_MIN - STEP / 2, -STEP), 1)
    lons = np.round(np.arange(LON_MIN, LON_MAX + STEP / 2, STEP), 1)
    return lats, lons


def cell_id(row: int, col: int, n_lon: int) -> int:
    """Identifiant stable d'une maille, en balayage ligne par ligne."""
    return row * n_lon + col


def cell_rowcol(cell_id: int, n_lon: int) -> tuple[int, int]:
    return divmod(cell_id, n_lon)


def land_mask(sample: np.ndarray) -> np.ndarray:
    """Mailles terrestres, déduites d'un échantillon ERA5-Land.

    ERA5-Land ne modélise que les surfaces continentales : les points marins
    sont livrés sans valeur. Une maille qui porte une valeur finie est donc
    terrestre.
    """
    return np.isfinite(np.asarray(sample, dtype=np.float64))
