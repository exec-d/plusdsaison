#!/usr/bin/env python3
"""Construit index/communes.bin et index/grid.bin.

À exécuter une fois, puis à chaque évolution du référentiel communal
(fusions, créations). Nécessite un échantillon ERA5-Land pour connaître le
masque terre et l'orographie du modèle. Sans `--sample`, cet échantillon est
téléchargé depuis Copernicus et mis en cache.

    python build_index.py --out .. --cache .cache
    python build_index.py --sample .cache/era5_land_static.nc --out ..
"""

import argparse
from pathlib import Path

import numpy as np
import requests
import xarray as xr

from plusdsaison.cds import retrieve_land_probe, retrieve_static
from plusdsaison.communes import (
    ELEVATION_BATCH,
    MAX_DISTANCE_KM,
    attach_to_land_cells,
    fetch_communes,
    fetch_elevations,
)
from plusdsaison.grid import cell_id, grid_axes, land_mask
from plusdsaison.index_io import GridCell, write_commune_index, write_grid_index

# Pesanteur normale, celle qu'utilise l'IFS pour passer du géopotentiel de
# surface (m²/s²) à une altitude en mètres.
GRAVITE = 9.80665


def charger_orographie(chemin: Path) -> np.ndarray:
    """Orographie du modèle, en mètres, depuis le géopotentiel de surface."""
    jeu = xr.open_dataset(chemin)
    if "z" not in jeu.data_vars:
        raise ValueError(
            f"{chemin} ne contient pas le géopotentiel 'z' "
            f"(variables présentes : {list(jeu.data_vars)})"
        )
    # Le champ est statique mais reste livré avec un axe temps d'une seule
    # valeur : le réduire plutôt que de supposer son nom.
    return jeu["z"].squeeze(drop=True).values / GRAVITE


def charger_masque_terre(chemin: Path) -> np.ndarray:
    """Mailles effectivement servies par ERA5-Land, depuis une sonde.

    Le géopotentiel ne convient pas pour cet usage : il est fini partout,
    mer comprise. `land_sea_mask` non plus, qui désigne 143 mailles de
    différence. Seule une variable météorologique dit où il y a des données.
    """
    jeu = xr.open_dataset(chemin)
    nom = list(jeu.data_vars)[0]
    return land_mask(jeu[nom].squeeze(drop=True).values)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--sample", type=Path,
                         help="NetCDF ERA5-Land statique ; téléchargé si absent")
    parseur.add_argument("--probe", type=Path,
                         help="NetCDF d'une journée servant de masque terre")
    parseur.add_argument("--out", required=True, type=Path,
                         help="racine du dépôt de données")
    parseur.add_argument("--cache", type=Path, default=Path(".cache"))
    args = parseur.parse_args()

    if args.sample is None or args.probe is None:
        import cdsapi

        client = cdsapi.Client()
        if args.sample is None:
            print("téléchargement de l'échantillon statique…", flush=True)
            args.sample = retrieve_static(client, args.cache)
        if args.probe is None:
            print("téléchargement de la sonde du masque terre…", flush=True)
            args.probe = retrieve_land_probe(client, args.cache)

    lats, lons = grid_axes()
    forme_attendue = (len(lats), len(lons))

    orographie = charger_orographie(args.sample)
    if orographie.shape != forme_attendue:
        raise ValueError(
            f"l'échantillon statique fait {orographie.shape}, "
            f"la grille attend {forme_attendue}"
        )

    masque = charger_masque_terre(args.probe)
    if masque.shape != forme_attendue:
        raise ValueError(
            f"la sonde fait {masque.shape}, la grille attend {forme_attendue}"
        )

    mailles = []
    for ligne in range(masque.shape[0]):
        for colonne in range(masque.shape[1]):
            if not masque[ligne, colonne]:
                continue
            mailles.append(
                GridCell(
                    cell_id=cell_id(ligne, colonne, len(lons)),
                    lat=float(lats[ligne]),
                    lon=float(lons[colonne]),
                    orography_m=float(orographie[ligne, colonne]),
                )
            )
    print(f"{len(mailles)} mailles terrestres retenues")

    session = requests.Session()
    communes = fetch_communes(session)
    print(f"{len(communes)} communes récupérées")

    def avancement(traitees: int, total: int) -> None:
        if traitees % 5000 < ELEVATION_BATCH or traitees == total:
            print(f"  altitudes {traitees}/{total}", flush=True)

    fetch_elevations(session, communes, progres=avancement)
    print("altitudes récupérées")

    attach_to_land_cells(communes, lats, lons, masque)
    eloignees = [c for c in communes if c.distance_km and c.distance_km > MAX_DISTANCE_KM]
    print(f"{len(eloignees)} communes à plus de {MAX_DISTANCE_KM:.0f} km de leur maille")

    index = args.out / "index"
    index.mkdir(parents=True, exist_ok=True)
    write_grid_index(index / "grid.bin", mailles)
    write_commune_index(index / "communes.bin", communes)
    print(f"index écrit dans {index}")


if __name__ == "__main__":
    main()
