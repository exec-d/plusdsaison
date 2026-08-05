"""Le manifeste est le seul fichier que l'application relit à chaque
lancement : il décide de l'invalidation du cache."""

import json
from datetime import date

import pytest

from plusdsaison.manifest import read_manifest, write_manifest


def test_le_manifeste_porte_tout_ce_qui_invalide_le_cache(tmp_path):
    chemin = tmp_path / "manifest.json"
    write_manifest(
        chemin, format_version=1, n_cells=6500, n_communes=34945,
        updated=date(2026, 8, 4), first_day=0, last_day=27974,
    )

    manifeste = read_manifest(chemin)

    assert manifeste["format_version"] == 1
    assert manifeste["n_cells"] == 6500
    assert manifeste["n_communes"] == 34945
    assert manifeste["updated"] == "2026-08-04"
    assert manifeste["first_day"] == 0
    assert manifeste["last_day"] == 27974


def test_le_manifeste_est_du_json_lisible(tmp_path):
    chemin = tmp_path / "manifest.json"
    write_manifest(
        chemin, format_version=1, n_cells=1, n_communes=1,
        updated=date(2026, 1, 1), first_day=0, last_day=1,
    )
    # Relu par un client tiers sans notre code : doit rester du JSON nu.
    assert json.loads(chemin.read_text())["format_version"] == 1


def test_une_periode_incoherente_est_refusee(tmp_path):
    with pytest.raises(ValueError, match="antérieur"):
        write_manifest(
            tmp_path / "m.json", format_version=1, n_cells=1, n_communes=1,
            updated=date(2026, 1, 1), first_day=100, last_day=50,
        )
