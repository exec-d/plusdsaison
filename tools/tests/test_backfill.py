"""La liste des requêtes, vérifiée sans toucher au réseau.

C'est elle qui décide du volume : une erreur de bornes ici coûterait des
heures de file d'attente Copernicus avant d'être vue.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backfill import PARALLELISME_DEFAUT, taches_de_telechargement


def test_quatre_trimestres_et_deux_precipitations_par_annee():
    taches = taches_de_telechargement(2020, 2020)
    temperatures = [t for t in taches if t[0] == "temperature"]
    precipitations = [t for t in taches if t[0] == "precipitation"]

    # Quatre trimestres d'horaire, dont Tmin, Tmax et Tmoy sont ensuite
    # calculées localement. Une année entière d'un coup dépasse la limite de
    # coût de Copernicus.
    assert len(temperatures) == 4
    # Deux et non une : le cumul d'une journée est daté du lendemain, donc le
    # 31 décembre 2020 ne se lit que dans le fichier de 2021.
    assert len(precipitations) == 2
    assert {t[3] for t in precipitations} == {2020, 2021}


def test_les_quatre_trimestres_sont_distincts():
    # Quatre requêtes identiques rendraient quatre fois le même trimestre, et
    # les trois quarts de l'année seraient absents sans que rien ne le dise.
    taches = taches_de_telechargement(2020, 2020)
    trimestres = [t[2] for t in taches if t[0] == "temperature"]
    assert sorted(trimestres) == [1, 2, 3, 4]


def test_le_volume_croit_avec_la_periode():
    assert len(taches_de_telechargement(2020, 2020)) == 6
    assert len(taches_de_telechargement(2019, 2020)) == 11
    # 1950-2025 : 76 ans × 4 trimestres + 77 années de précipitations.
    assert len(taches_de_telechargement(1950, 2025)) == 76 * 4 + 77


def test_aucune_tache_en_double():
    # Une requête soumise deux fois attend deux fois en file pour le même
    # fichier, et la file est 90 % du temps total.
    taches = taches_de_telechargement(1950, 2025)
    assert len(taches) == len(set(taches))


def test_le_parallelisme_par_defaut_reste_prudent():
    # Copernicus refuse les requêtes au-delà d'une limite de travaux
    # simultanés par compte, au lieu de les mettre en attente.
    assert 2 <= PARALLELISME_DEFAUT <= 10


def test_un_trimestre_a_venir_n_est_pas_demande():
    from datetime import date

    from backfill import _trimestre_a_venir

    aout = date(2026, 8, 7)
    # Passés ou en cours : demandés.
    assert not _trimestre_a_venir(2026, 1, aout)
    assert not _trimestre_a_venir(2026, 3, aout), "juillet-septembre a commencé"
    # À venir : Copernicus rendrait une erreur, pas un fichier vide. Sans ce
    # filtre le rafraîchissement quotidien casse d'octobre à décembre.
    assert _trimestre_a_venir(2026, 4, aout)
    assert _trimestre_a_venir(2027, 1, aout)
    # Une année entièrement passée reste entièrement demandée.
    assert not any(_trimestre_a_venir(2025, t, aout) for t in (1, 2, 3, 4))
