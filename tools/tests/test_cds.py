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
    QUARTERS,
    STATIC_DATASET,
    build_hourly_temperature_request,
    build_land_probe_request,
    build_precipitation_request,
    build_request,
    build_static_request,
    retrieve_hourly_temperature,
    retrieve_land_probe,
    retrieve_precipitation_year,
    retrieve_static,
    retrieve_year,
)


def test_la_temperature_vient_du_dataset_horaire():
    # Le dataset quotidien dérivé livre Tmin/Tmax/Tmoy toutes calculées, pour
    # un vingt-quatrième du volume — mais sa file met 6 à 16 heures par
    # requête et n'en sert qu'une à la fois. Reconstruire 1950-2025 y prendrait
    # des semaines. Le volume est le prix de l'attente évitée.
    requete = build_hourly_temperature_request(2020, 1)
    assert requete["variable"] == ["2m_temperature"]
    assert len(requete["time"]) == 24


def test_un_trimestre_par_requete():
    # Une année entière d'horaire est refusée par Copernicus (« your request
    # is too large »), un trimestre passe. Mesuré, pas déduit.
    for trimestre in (1, 2, 3, 4):
        requete = build_hourly_temperature_request(2020, trimestre)
        assert len(requete["month"]) == 3
    assert build_hourly_temperature_request(2020, 1)["month"] == ["01", "02", "03"]
    assert build_hourly_temperature_request(2020, 4)["month"] == ["10", "11", "12"]


def test_les_quatre_trimestres_couvrent_les_douze_mois():
    # Un mois oublié serait un trou de trente jours dans chaque année, que
    # rien d'autre ne signalerait.
    mois = [m for t in (1, 2, 3, 4) for m in QUARTERS[t]]
    assert sorted(mois) == [f"{m:02d}" for m in range(1, 13)]


def test_un_trimestre_deja_en_cache_n_est_pas_retelecharge(tmp_path):
    class ClientQuiExplose:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("le cache aurait dû éviter cet appel")

    attendu = tmp_path / "2m_temperature_hourly_2020_T3.nc"
    attendu.write_bytes(b"deja la")

    assert retrieve_hourly_temperature(ClientQuiExplose(), 2020, 3, tmp_path) == attendu


def test_le_trimestre_horaire_passe_par_le_dataset_rapide(tmp_path):
    appels = []

    class ClientEnregistreur:
        def retrieve(self, dataset, requete, cible):
            appels.append(dataset)
            Path(cible).write_bytes(b"telecharge")

    retrieve_hourly_temperature(ClientEnregistreur(), 2020, 1, tmp_path)
    assert appels == [STATIC_DATASET]


def test_la_requete_cible_le_dataset_quotidien():
    # Conservé pour l'orographie et la sonde du masque terre, qui n'ont pas
    # d'équivalent ailleurs.
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


def test_la_sonde_du_masque_terre_ne_couvre_qu_un_jour():
    # Relever quelles mailles sont servies ne demande qu'une journée, mais
    # sur toute l'emprise.
    requete = build_land_probe_request()
    assert requete["month"] == ["07"]
    assert requete["day"] == ["15"]
    assert requete["area"] == AREA
    assert requete["variable"] == ["2m_temperature"]


def test_la_sonde_du_masque_terre_est_mise_en_cache(tmp_path):
    class ClientQuiExplose:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("le cache aurait dû éviter cet appel")

    attendu = tmp_path / "land_probe.nc"
    attendu.write_bytes(b"deja la")

    assert retrieve_land_probe(ClientQuiExplose(), tmp_path) == attendu


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


def test_les_trois_requetes_refusent_l_archive():
    """Aucune des trois ne doit laisser Copernicus livrer un zip.

    `xarray` ne sait pas ouvrir une archive, et le constructeur annuel est
    celui dont l'échec coûte le plus : une année complète passe des heures en
    file d'attente avant qu'on découvre le format du fichier reçu. Il était le
    seul des trois à omettre le champ, ce qu'aucun test ne voyait.
    """
    requetes = [
        build_request("2m_temperature", "daily_mean", 2020),
        build_static_request(),
        build_precipitation_request(2020),
    ]
    for requete in requetes:
        assert requete["download_format"] == "unarchived", requete
