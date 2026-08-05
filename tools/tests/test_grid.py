"""La grille ERA5-Land découpe la France en mailles de 0,1°. Le cell_id est
publié dans les fichiers et référencé par l'index : sa formule est un
contrat, pas un détail d'implémentation."""

import numpy as np
import pytest

from plusdsaison.grid import (
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    STEP,
    cell_id,
    cell_rowcol,
    grid_axes,
    land_mask,
)


def test_les_axes_couvrent_la_france_metropolitaine():
    lats, lons = grid_axes()
    assert lats[0] == pytest.approx(LAT_MAX)
    assert lats[-1] == pytest.approx(LAT_MIN)
    assert lons[0] == pytest.approx(LON_MIN)
    assert lons[-1] == pytest.approx(LON_MAX)


def test_les_latitudes_decroissent_comme_dans_le_netcdf():
    # ERA5 publie ses latitudes du nord vers le sud ; suivre cette convention
    # évite un retournement de tableau à chaque lecture.
    lats, _ = grid_axes()
    assert (np.diff(lats) < 0).all()


def test_le_pas_vaut_un_dixieme_de_degre():
    lats, lons = grid_axes()
    assert np.diff(lons)[0] == pytest.approx(STEP)
    assert -np.diff(lats)[0] == pytest.approx(STEP)


def test_les_coordonnees_sont_arrondies_au_dixieme():
    # Sans arrondi explicite, arange produit 5.699999999999999 et le
    # rapprochement avec les coordonnées du NetCDF échoue.
    lats, lons = grid_axes()
    assert (np.abs(lons * 10 - np.rint(lons * 10)) < 1e-9).all()
    assert (np.abs(lats * 10 - np.rint(lats * 10)) < 1e-9).all()


def test_cell_id_est_reversible():
    n_lon = 156
    assert cell_id(0, 0, n_lon) == 0
    assert cell_id(0, 5, n_lon) == 5
    assert cell_id(3, 2, n_lon) == 3 * n_lon + 2
    assert cell_rowcol(cell_id(7, 11, n_lon), n_lon) == (7, 11)


def test_le_masque_terre_exclut_les_mailles_sans_valeur():
    # ERA5-Land est masqué sur mer : les points marins arrivent en NaN.
    echantillon = np.array([[1.0, np.nan], [np.nan, 4.0]])
    assert land_mask(echantillon).tolist() == [[True, False], [False, True]]
