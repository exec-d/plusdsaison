#!/usr/bin/env python3
"""Construit index/communes.bin et index/grid.bin.

À exécuter une fois, puis à chaque évolution du référentiel communal
(fusions, créations). Nécessite un échantillon ERA5-Land pour connaître le
masque terre et l'orographie du modèle. Sans `--sample`, cet échantillon est
téléchargé depuis Copernicus et mis en cache.

    python build_index.py --out .. --cache .cache
    python build_index.py --sample .cache/era5_land_static.nc --out ..

Quand seul le référentiel communal a changé — le cas courant, les communes
fusionnant tous les ans quand la grille ne bouge jamais — `--reuse-grid`
repart du `grid.bin` publié et ne touche pas à Copernicus :

    python build_index.py --reuse-grid --out .. --cache .cache
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
from plusdsaison.grid import cell_id, cell_rowcol, grid_axes, land_mask
from plusdsaison.index_io import (
    GridCell,
    read_grid_index,
    write_commune_index,
    write_grid_index,
)

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


def relire_la_grille(chemin: Path, forme: tuple[int, int]):
    """Les mailles et leur masque, depuis un `grid.bin` déjà publié.

    L'emprise ERA5-Land est figée et l'orographie du modèle ne bouge pas ; le
    référentiel communal, lui, change tous les ans. Refaire deux requêtes
    Copernicus pour redécouvrir une grille qu'on a déjà publiée n'apprend rien
    et occupe une file dont un backfill peut avoir besoin au même moment.

    Le masque se reconstitue exactement : une maille est terrestre si et
    seulement si elle figure dans le fichier.
    """
    mailles = read_grid_index(chemin)
    masque = np.zeros(forme, dtype=bool)
    for maille in mailles:
        ligne, colonne = cell_rowcol(maille.cell_id, forme[1])
        masque[ligne, colonne] = True
    return mailles, masque


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--sample", type=Path,
                         help="NetCDF ERA5-Land statique ; téléchargé si absent")
    parseur.add_argument("--probe", type=Path,
                         help="NetCDF d'une journée servant de masque terre")
    parseur.add_argument("--reuse-grid", action="store_true",
                         help="repartir du grid.bin publié plutôt que de "
                              "Copernicus ; ne réécrit que communes.bin")
    parseur.add_argument("--out", required=True, type=Path,
                         help="racine du dépôt de données")
    parseur.add_argument("--cache", type=Path, default=Path(".cache"))
    args = parseur.parse_args()

    lats, lons = grid_axes()
    forme_attendue = (len(lats), len(lons))

    if args.reuse_grid:
        mailles, masque = relire_la_grille(
            args.out / "index" / "grid.bin", forme_attendue
        )
    else:
        if args.sample is None or args.probe is None:
            import cdsapi

            client = cdsapi.Client()
            if args.sample is None:
                print("téléchargement de l'échantillon statique…", flush=True)
                args.sample = retrieve_static(client, args.cache)
            if args.probe is None:
                print("téléchargement de la sonde du masque terre…", flush=True)
                args.probe = retrieve_land_probe(client, args.cache)

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

    # Rattacher avant de relever les altitudes : l'orographie de la maille
    # sert de repli pour les communes que le service altimétrique ne couvre
    # pas, et elle n'est connue qu'une fois le rattachement fait.
    attach_to_land_cells(communes, lats, lons, masque)
    eloignees = [c for c in communes if c.distance_km and c.distance_km > MAX_DISTANCE_KM]
    print(f"{len(eloignees)} communes à plus de {MAX_DISTANCE_KM:.0f} km de leur maille")

    def avancement(traitees: int, total: int) -> None:
        if traitees % 5000 < ELEVATION_BATCH or traitees == total:
            print(f"  altitudes {traitees}/{total}", flush=True)

    args.cache.mkdir(parents=True, exist_ok=True)
    fetch_elevations(
        session, communes, progres=avancement, cache=args.cache / "altitudes.json"
    )

    # Une altitude inconnue prend celle du modèle : la correction
    # altitudinale devient alors neutre, ce qui est le comportement honnête
    # quand on ignore la hauteur réelle — plutôt qu'un zéro qui la fausserait.
    orographies = {m.cell_id: m.orography_m for m in mailles}
    sans_altitude = [c for c in communes if c.altitude is None]
    for commune in sans_altitude:
        commune.altitude = orographies.get(commune.cell_id, 0.0)
    print(
        f"altitudes récupérées ({len(sans_altitude)} hors couverture, "
        "repliées sur l'orographie du modèle)"
    )

    index = args.out / "index"
    index.mkdir(parents=True, exist_ok=True)
    if not args.reuse_grid:
        write_grid_index(index / "grid.bin", mailles)
    write_commune_index(index / "communes.bin", communes)
    print(f"index écrit dans {index}")


if __name__ == "__main__":
    main()
