"""La validation est ce qui empêche de publier des données silencieusement
fausses : son seuil doit bloquer, pas seulement avertir."""

import numpy as np
import pytest

from plusdsaison.validate import (
    BIAS_THRESHOLD_C,
    compare_series,
    departement_verdict,
    verdict_line,
)


def test_deux_series_identiques_ne_montrent_aucun_ecart():
    serie = np.array([1.0, 2.0, 3.0])
    resultat = compare_series(serie, serie)
    assert resultat["bias"] == pytest.approx(0.0)
    assert resultat["rmse"] == pytest.approx(0.0)
    assert resultat["n"] == 3


def test_le_biais_est_signe_modele_moins_station():
    # Un modèle systématiquement trop chaud doit donner un biais positif.
    resultat = compare_series(np.array([11.0, 12.0]), np.array([10.0, 11.0]))
    assert resultat["bias"] == pytest.approx(1.0)


def test_les_jours_manquants_sont_ecartes_des_deux_cotes():
    modele = np.array([10.0, np.nan, 12.0])
    station = np.array([10.0, 11.0, np.nan])
    resultat = compare_series(modele, station)
    assert resultat["n"] == 1
    assert resultat["bias"] == pytest.approx(0.0)


def test_aucun_jour_commun_donne_un_effectif_nul():
    resultat = compare_series(np.array([np.nan]), np.array([1.0]))
    assert resultat["n"] == 0
    assert np.isnan(resultat["bias"])


def test_un_departement_dans_la_tolerance_est_accepte():
    verdict = departement_verdict([
        {"bias": 0.4, "rmse": 1.1, "n": 300},
        {"bias": -0.6, "rmse": 1.3, "n": 300},
    ])
    assert verdict["ok"] is True
    assert verdict["bias"] == pytest.approx(-0.1)


def test_un_departement_hors_tolerance_est_rejete():
    verdict = departement_verdict([
        {"bias": 2.0, "rmse": 2.4, "n": 300},
        {"bias": 1.8, "rmse": 2.2, "n": 300},
    ])
    assert verdict["ok"] is False
    assert abs(verdict["bias"]) > BIAS_THRESHOLD_C


def test_les_stations_sans_jour_commun_ne_pesent_pas_dans_la_moyenne():
    verdict = departement_verdict([
        {"bias": 0.2, "rmse": 1.0, "n": 300},
        {"bias": float("nan"), "rmse": float("nan"), "n": 0},
    ])
    assert verdict["ok"] is True
    assert verdict["bias"] == pytest.approx(0.2)


def test_un_departement_sans_aucune_station_est_signale():
    verdict = departement_verdict([])
    assert verdict["ok"] is False
    assert verdict["n_stations"] == 0


def test_un_departement_sans_station_ne_se_dit_pas_hors_tolerance():
    # Six jours durant, le journal a annoncé « biais +nan °C — HORS TOLÉRANCE »
    # pour un département dont Météo-France ne publie rien sous ce code. Les
    # deux verdicts appellent des gestes opposés : corriger les données, ou
    # corriger la question posée.
    ligne = verdict_line("20", departement_verdict([]))
    assert "comparaison impossible" in ligne
    assert "HORS TOLÉRANCE" not in ligne
    assert "nan" not in ligne


def test_un_departement_hors_tolerance_annonce_son_biais():
    ligne = verdict_line("74", departement_verdict([{"bias": 2.0, "rmse": 2.4, "n": 300}]))
    assert "HORS TOLÉRANCE" in ligne
    assert "+2.00 °C" in ligne


def test_un_departement_conforme_annonce_sur_combien_de_stations():
    ligne = verdict_line("01", departement_verdict([
        {"bias": 0.4, "rmse": 1.1, "n": 300},
        {"bias": -0.6, "rmse": 1.3, "n": 300},
    ]))
    assert "OK" in ligne
    assert "2 stations" in ligne


def test_une_seule_station_ne_prend_pas_le_pluriel():
    ligne = verdict_line("29", departement_verdict([{"bias": 0.2, "rmse": 1.0, "n": 300}]))
    assert "1 station —" in ligne
