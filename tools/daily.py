#!/usr/bin/env python3
"""Rafraîchit l'année en cours, du 1er janvier au dernier jour disponible.

Le dataset accuse six jours de traitement : on demande l'année entière et on
tronque à la dernière journée effectivement remplie, plutôt que de calculer
une date de fin qui se désynchroniserait au moindre retard de Copernicus.

    python daily.py --out .. --cache .cache
"""

import argparse
from datetime import date
from pathlib import Path

import cdsapi
import numpy as np

from backfill import assembler
from plusdsaison.binary import DEFAULT_SCALE, date_to_day, encode_series
from plusdsaison.index_io import read_grid_index
from plusdsaison.quantize import MISSING, dequantize


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--out", required=True, type=Path)
    parseur.add_argument("--cache", type=Path, default=Path(".cache"))
    args = parseur.parse_args()

    annee = date.today().year
    mailles = read_grid_index(args.out / "index" / "grid.bin")

    # Le cache annuel du backfill garderait une version périmée de l'année
    # en cours : on le purge avant de redemander.
    for reste in args.cache.glob(f"*_{annee}.nc"):
        reste.unlink()

    premier_jour = date_to_day(date(annee, 1, 1))
    n_jours = date_to_day(date(annee, 12, 31)) - premier_jour + 1

    accumulateur = assembler(
        cdsapi.Client(), annee, annee, mailles, args.cache, premier_jour, n_jours
    )

    # Dernier jour où au moins une maille porte une température : au-delà,
    # Copernicus n'a pas encore produit.
    remplis = (accumulateur[0] != MISSING).any(axis=0)
    if not remplis.any():
        raise SystemExit(f"aucune donnée disponible pour {annee}")
    dernier = int(np.nonzero(remplis)[0][-1])
    print(f"{dernier + 1} jours disponibles pour {annee}")

    racine = args.out / "current"
    racine.mkdir(parents=True, exist_ok=True)

    for position, maille in enumerate(mailles):
        series = [
            dequantize(accumulateur[variable, position, : dernier + 1], DEFAULT_SCALE)
            for variable in range(accumulateur.shape[0])
        ]
        (racine / f"{maille.cell_id}.bin").write_bytes(
            encode_series(maille.cell_id, premier_jour, series)
        )

    print(f"{len(mailles)} fichiers écrits dans {racine}")


if __name__ == "__main__":
    main()
