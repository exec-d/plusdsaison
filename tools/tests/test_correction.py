"""Correction altitudinale par gradient adiabatique.

Ces valeurs golden sont partagées avec l'implémentation Dart de
l'application : les deux doivent produire exactement les mêmes nombres."""

import numpy as np
import pytest

from plusdsaison.correction import LAPSE_RATE_C_PER_M, correct_temperature


def test_le_gradient_vaut_le_standard_atmospherique():
    assert LAPSE_RATE_C_PER_M == 0.0065


def test_une_cible_plus_haute_que_le_modele_est_plus_froide():
    # 600 m au-dessus de l'orographie × 0,0065 = 3,9 °C de moins.
    corrigee = correct_temperature(np.array([20.0]), 1800.0, 1200.0)
    assert corrigee[0] == pytest.approx(16.1, abs=1e-9)


def test_une_cible_plus_basse_que_le_modele_est_plus_chaude():
    corrigee = correct_temperature(np.array([10.0]), 200.0, 500.0)
    assert corrigee[0] == pytest.approx(11.95, abs=1e-9)


def test_altitudes_egales_ne_changent_rien():
    valeurs = np.array([-3.0, 0.0, 21.4])
    assert correct_temperature(valeurs, 350.0, 350.0) == pytest.approx(valeurs)


def test_les_valeurs_manquantes_le_restent():
    corrigee = correct_temperature(np.array([np.nan, 10.0]), 1000.0, 500.0)
    assert np.isnan(corrigee[0])
    assert corrigee[1] == pytest.approx(6.75, abs=1e-9)
