#!/usr/bin/env python3
"""Bascule l'année close de current/ vers history.bin.

À exécuter en janvier, une fois que Copernicus a consolidé décembre. Relit
chaque history.bin, y concatène l'année close, et réécrit le fichier.

L'opération est idempotente : une année déjà intégrée est détectée par la
longueur de la série et laissée en place.

    python rollover.py --out .. --year 2026
"""

import argparse
from datetime import date
from pathlib import Path

import numpy as np

from plusdsaison.binary import date_to_day, decode_series, encode_series
from plusdsaison.index_io import read_grid_index
from plusdsaison.manifest import read_manifest, write_manifest


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--out", required=True, type=Path)
    parseur.add_argument("--year", required=True, type=int)
    args = parseur.parse_args()

    fin_annee = date_to_day(date(args.year, 12, 31))
    mailles = read_grid_index(args.out / "index" / "grid.bin")
    bascules = 0

    for maille in mailles:
        chemin_histoire = args.out / "grid" / str(maille.cell_id) / "history.bin"
        chemin_courant = args.out / "current" / f"{maille.cell_id}.bin"
        if not chemin_courant.exists():
            continue

        entete_h, colonnes_h = decode_series(chemin_histoire.read_bytes())
        dernier_jour_connu = entete_h.start_day + entete_h.n_days - 1
        if dernier_jour_connu >= fin_annee:
            continue  # déjà intégrée

        entete_c, colonnes_c = decode_series(chemin_courant.read_bytes())
        # On ne reprend que les jours postérieurs à ce que l'historique
        # contient déjà, pour ne jamais dupliquer une journée.
        decalage = dernier_jour_connu + 1 - entete_c.start_day
        if decalage < 0 or decalage >= entete_c.n_days:
            continue

        fusionnees = [
            np.concatenate([h, c[decalage:]]) for h, c in zip(colonnes_h, colonnes_c)
        ]
        chemin_histoire.write_bytes(
            encode_series(maille.cell_id, entete_h.start_day, fusionnees)
        )
        bascules += 1

    if bascules:
        manifeste = read_manifest(args.out / "manifest.json")
        write_manifest(
            args.out / "manifest.json",
            format_version=manifeste["format_version"],
            n_cells=manifeste["n_cells"],
            n_communes=manifeste["n_communes"],
            updated=date.today(),
            first_day=manifeste["first_day"],
            last_day=fin_annee,
        )
    print(f"{bascules} mailles basculées pour {args.year}")


if __name__ == "__main__":
    main()
