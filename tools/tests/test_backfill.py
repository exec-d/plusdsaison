"""La liste des requêtes, vérifiée sans toucher au réseau.

C'est elle qui décide du volume : une erreur de bornes ici coûterait des
heures de file d'attente Copernicus avant d'être vue.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backfill import PARALLELISME_DEFAUT, taches_de_telechargement


def test_quatre_series_par_annee():
    taches = taches_de_telechargement(2020, 2020)
    temperatures = [t for t in taches if t[0] == "temperature"]
    precipitations = [t for t in taches if t[0] == "precipitation"]

    assert len(temperatures) == 3, "Tmin, Tmax, Tmoy"
    # Deux et non une : le cumul d'une journée est daté du lendemain, donc le
    # 31 décembre 2020 ne se lit que dans le fichier de 2021.
    assert len(precipitations) == 2
    assert {t[3] for t in precipitations} == {2020, 2021}


def test_les_trois_temperatures_sont_distinctes():
    # Trois requêtes identiques rendraient trois fois la même série, et le
    # graphe afficherait une courbe plate là où il faut un minimum, une
    # moyenne et un maximum.
    taches = taches_de_telechargement(2020, 2020)
    statistiques = [t[2] for t in taches if t[0] == "temperature"]
    assert len(set(statistiques)) == 3


def test_le_volume_croit_avec_la_periode():
    assert len(taches_de_telechargement(2020, 2020)) == 5
    assert len(taches_de_telechargement(2019, 2020)) == 9
    # 1950-2025 : 76 ans × 3 températures + 77 années de précipitations.
    assert len(taches_de_telechargement(1950, 2025)) == 76 * 3 + 77


def test_aucune_tache_en_double():
    # Une requête soumise deux fois attend deux fois en file pour le même
    # fichier, et la file est 90 % du temps total.
    taches = taches_de_telechargement(1950, 2025)
    assert len(taches) == len(set(taches))


def test_le_parallelisme_par_defaut_reste_prudent():
    # Copernicus refuse les requêtes au-delà d'une limite de travaux
    # simultanés par compte, au lieu de les mettre en attente.
    assert 2 <= PARALLELISME_DEFAUT <= 10
