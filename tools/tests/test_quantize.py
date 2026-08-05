"""Le format publié stocke des entiers 16 bits ; ces tests fixent le contrat
de conversion, y compris ses pertes acceptées et ses cas limites."""

import numpy as np
import pytest

from plusdsaison.quantize import INT16_MAX, MISSING, dequantize, quantize


def test_aller_retour_conserve_le_dixieme():
    valeurs = np.array([-12.3, 0.0, 41.7, 3.14159])
    restituees = dequantize(quantize(valeurs, 10), 10)
    assert restituees == pytest.approx([-12.3, 0.0, 41.7, 3.1], abs=0.05)


def test_valeur_manquante_devient_sentinelle():
    codes = quantize(np.array([np.nan]), 10)
    assert codes[0] == MISSING
    assert np.isnan(dequantize(codes, 10)[0])


def test_valeur_hors_plage_est_bornee_sans_deborder():
    # 9999 °C × 10 = 99 990, très au-delà de la capacité d'un int16.
    codes = quantize(np.array([9999.0]), 10)
    assert codes[0] == INT16_MAX


def test_le_bornage_ne_produit_jamais_la_sentinelle():
    # -32768 est réservé au manquant : une valeur très négative doit s'arrêter
    # à -32767, sinon une mesure réelle serait relue comme absente.
    codes = quantize(np.array([-9999.0]), 10)
    assert codes[0] == -32767
    assert not np.isnan(dequantize(codes, 10)[0])


def test_tableau_entierement_manquant():
    codes = quantize(np.array([np.nan, np.nan]), 10)
    assert (codes == MISSING).all()


def test_le_type_de_sortie_est_bien_int16():
    assert quantize(np.array([1.0]), 10).dtype == np.int16
