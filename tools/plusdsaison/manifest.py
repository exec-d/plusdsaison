"""Manifeste publié à la racine du dépôt.

Quelques centaines d'octets relus par l'application à chaque lancement.
`format_version` est ce qui invalide le cache local : l'incrémenter force
tous les clients à retélécharger.
"""

import json
from datetime import date
from pathlib import Path


def write_manifest(
    path: Path,
    *,
    format_version: int,
    n_cells: int,
    n_communes: int,
    updated: date,
    first_day: int,
    last_day: int,
) -> None:
    if last_day < first_day:
        raise ValueError(
            f"dernier jour {last_day} antérieur au premier jour {first_day}"
        )
    contenu = {
        "format_version": format_version,
        "n_cells": n_cells,
        "n_communes": n_communes,
        "updated": updated.isoformat(),
        "first_day": first_day,
        "last_day": last_day,
    }
    Path(path).write_text(json.dumps(contenu, indent=2, ensure_ascii=False) + "\n")


def read_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text())
