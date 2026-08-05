"""Le client CDS est testé sur la forme de ses requêtes et sur sa reprise ;
aucun test ne contacte Copernicus."""

from pathlib import Path

import pytest

from plusdsaison.cds import (
    AREA,
    DATASET,
    PRECIPITATION_DATASET,
    PRECIPITATION_DAY_SHIFT,
    PRECIPITATION_FACTOR,
    STATIC_DATASET,
    build_precipitation_request,
    build_request,
    build_static_request,
    retrieve_precipitation_year,
    retrieve_static,
    retrieve_year,
)


def test_la_requete_cible_le_dataset_quotidien():
    # Le dataset horaire imposerait de télécharger 24 fois plus de données
    # pour les agréger nous-mêmes.
    assert DATASET == "derived-era5-land-daily-statistics"


def test_l_emprise_suit_la_convention_nord_ouest_sud_est():
    # Copernicus attend [Nord, Ouest, Sud, Est] : toute autre lecture
    # ramènerait une zone vide ou décalée.
    assert AREA == [51.5, -5.5, 41.0, 10.0]


def test_la_requete_couvre_toute_l_annee():
    requete = build_request("2m_temperature", "daily_minimum", 2020)
    assert requete["year"] == "2020"
    assert len(requete["month"]) == 12
    assert len(requete["day"]) == 31
    assert requete["daily_statistic"] == "daily_minimum"
    assert requete["variable"] == ["2m_temperature"]
    assert requete["data_format"] == "netcdf"


def test_l_heure_locale_est_celle_de_paris():
    # Un découpage des journées en UTC décalerait les minima nocturnes
    # d'une heure et fausserait le comptage des jours de gel.
    requete = build_request("2m_temperature", "daily_minimum", 2020)
    assert requete["time_zone"] == "utc+01:00"


def test_une_annee_deja_en_cache_n_est_pas_retelechargee(tmp_path):
    class ClientQuiExplose:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("le cache aurait dû éviter cet appel")

    attendu = tmp_path / "2m_temperature_daily_minimum_2020.nc"
    attendu.write_bytes(b"deja la")

    obtenu = retrieve_year(
        ClientQuiExplose(), "2m_temperature", "daily_minimum", 2020, tmp_path
    )

    assert obtenu == attendu


def test_un_telechargement_absent_declenche_l_appel(tmp_path):
    appels = []

    class ClientEnregistreur:
        def retrieve(self, dataset, requete, cible):
            appels.append((dataset, requete["year"]))
            Path(cible).write_bytes(b"telecharge")

    retrieve_year(ClientEnregistreur(), "2m_temperature", "daily_minimum", 2020, tmp_path)

    assert appels == [(DATASET, "2020")]


def test_les_champs_statiques_viennent_du_dataset_horaire():
    # Le dataset des statistiques quotidiennes n'expose aucune variable
    # statique : ni géopotentiel, ni masque terre-mer.
    assert STATIC_DATASET == "reanalysis-era5-land"


def test_la_requete_statique_ne_demande_qu_une_heure():
    # Le géopotentiel est invariant : demander plus d'une heure ne ferait
    # que gonfler le téléchargement.
    requete = build_static_request()
    assert requete["time"] == ["00:00"]
    assert requete["area"] == AREA


def test_la_requete_statique_ne_demande_qu_une_variable():
    # Copernicus livre une archive zip dès qu'une requête NetCDF porte
    # plusieurs variables, et xarray ne sait pas l'ouvrir.
    assert build_static_request()["variable"] == ["geopotential"]


def test_l_echantillon_statique_est_mis_en_cache(tmp_path):
    class ClientQuiExplose:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("le cache aurait dû éviter cet appel")

    attendu = tmp_path / "era5_land_static.nc"
    attendu.write_bytes(b"deja la")

    assert retrieve_static(ClientQuiExplose(), tmp_path) == attendu


def test_les_precipitations_ne_viennent_pas_du_dataset_quotidien():
    # Copernicus refuse les variables accumulées sur les statistiques
    # quotidiennes : « Daily statistics of accumulated variables are not
    # supported for this dataset, skipping: total_precipitation. »
    assert PRECIPITATION_DATASET != DATASET
    assert PRECIPITATION_DATASET == "reanalysis-era5-land"


def test_les_precipitations_se_lisent_a_une_seule_heure():
    # L'accumulation d'une journée entière se lit en une valeur : demander
    # les 24 heures multiplierait le volume sans rien apporter.
    requete = build_precipitation_request(2023)
    assert requete["time"] == ["00:00"]
    assert requete["variable"] == ["total_precipitation"]
    assert requete["year"] == "2023"
    assert len(requete["day"]) == 31


def test_le_cumul_est_date_du_lendemain():
    # La valeur du 2 janvier à 00:00 est le cumul du 1er janvier : sans ce
    # décalage, toute la pluie de France glisserait d'un jour.
    assert PRECIPITATION_DAY_SHIFT == -1


def test_le_facteur_de_precipitation_convertit_les_metres_en_millimetres():
    # Mesuré sur la Dombes en 2023 : 1 104 mm avec ce facteur.
    assert PRECIPITATION_FACTOR == 1000.0


def test_une_annee_de_precipitations_deja_en_cache_n_est_pas_retelechargee(tmp_path):
    class ClientQuiExplose:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("le cache aurait dû éviter cet appel")

    attendu = tmp_path / "total_precipitation_2023.nc"
    attendu.write_bytes(b"deja la")

    assert retrieve_precipitation_year(ClientQuiExplose(), 2023, tmp_path) == attendu
