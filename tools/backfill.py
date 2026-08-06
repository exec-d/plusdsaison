#!/usr/bin/env python3
"""Construit l'historique quotidien 1950 → N-1, maille par maille.

Télécharge quatre séries par année (Tmin, Tmax, Tmoy, précipitations),
les assemble en mémoire puis écrit un fichier binaire par maille terrestre.

Le téléchargement est reprenable : relancer la commande après une
interruption repart des années manquantes.

    python backfill.py --from 1950 --to 2025 --out .. --cache .cache
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import cdsapi
import numpy as np
import xarray as xr

from plusdsaison.binary import DEFAULT_SCALE, date_to_day, encode_series
from plusdsaison.cds import (
    PRECIPITATION_DAY_SHIFT,
    PRECIPITATION_FACTOR,
    STATISTICS,
    retrieve_precipitation_year,
    retrieve_year,
)
from plusdsaison.grid import grid_axes
from plusdsaison.index_io import read_commune_index, read_grid_index
from plusdsaison.manifest import write_manifest
from plusdsaison.quantize import MISSING, dequantize, quantize

# L'ordre des colonnes est celui du format publié : Tmin, Tmax, Tmoy, pluie.
TEMPERATURES = [
    ("2m_temperature", STATISTICS["min"]),
    ("2m_temperature", STATISTICS["max"]),
    ("2m_temperature", STATISTICS["mean"]),
]
N_SERIES = len(TEMPERATURES) + 1
COLONNE_PLUIE = len(TEMPERATURES)

KELVIN_OFFSET = 273.15


def _jours_du_fichier(jeu: xr.Dataset) -> np.ndarray:
    """Numéros de jours depuis l'époque, lus dans l'axe temps du fichier.

    Lire les dates plutôt que de les supposer : une année partielle ou un
    fichier tronqué se placerait sinon silencieusement au mauvais endroit.
    """
    nom = "valid_time" if "valid_time" in jeu.coords else "time"
    dates = np.asarray(jeu[nom].values, dtype="datetime64[D]")
    epoque = np.datetime64("1950-01-01", "D")
    return (dates - epoque).astype(np.int64)


def charger_temperatures(client, annee: int, cache: Path) -> list[tuple[np.ndarray, np.ndarray]]:
    """Trois séries (jours, tableau) en degrés Celsius pour l'année."""
    series = []
    for variable, statistique in TEMPERATURES:
        chemin = retrieve_year(client, variable, statistique, annee, cache)
        jeu = xr.open_dataset(chemin)
        nom = list(jeu.data_vars)[0]
        valeurs = jeu[nom].values.astype(np.float64) - KELVIN_OFFSET
        series.append((_jours_du_fichier(jeu), valeurs))
    return series


def charger_precipitations(client, annee: int, cache: Path) -> tuple[np.ndarray, np.ndarray]:
    """Cumuls en millimètres, et la journée que chacun décrit.

    Le fichier de l'année Y porte des valeurs datées du 1er janvier au
    31 décembre Y, mais chacune est le cumul de la veille : les journées
    couvertes vont donc du 31 décembre Y-1 au 30 décembre Y.
    """
    chemin = retrieve_precipitation_year(client, annee, cache)
    jeu = xr.open_dataset(chemin)
    nom = list(jeu.data_vars)[0]
    valeurs = jeu[nom].values.astype(np.float64) * PRECIPITATION_FACTOR
    if valeurs.ndim == 4:
        # Une seule heure demandée, mais l'axe reste présent.
        valeurs = valeurs.reshape((-1,) + valeurs.shape[-2:])
    return _jours_du_fichier(jeu) + PRECIPITATION_DAY_SHIFT, valeurs


def _deposer(accumulateur, colonne, jours, valeurs, lignes, colonnes, premier_jour):
    """Range un tableau (jours, lat, lon) dans l'accumulateur, aux bons jours.

    Les journées hors de la période demandée sont ignorées : le fichier de
    précipitations d'une année déborde d'un jour sur l'année précédente.
    """
    positions = jours - premier_jour
    retenus = (positions >= 0) & (positions < accumulateur.shape[2])
    if not retenus.any():
        return
    # Indexation vectorisée : (jours, mailles) en une seule passe, plutôt
    # qu'une boucle Python sur plus de 11 000 mailles par année.
    extrait = valeurs[retenus][:, lignes, colonnes]
    accumulateur[colonne][:, positions[retenus]] = quantize(extrait.T, DEFAULT_SCALE)


def positions_des_mailles(mailles, n_lon: int) -> tuple[np.ndarray, np.ndarray]:
    """Lignes et colonnes de grille des mailles, en tableaux."""
    return (
        np.array([m.cell_id // n_lon for m in mailles]),
        np.array([m.cell_id % n_lon for m in mailles]),
    )


#: Combien de requêtes attendent chez Copernicus en même temps.
#
# Ce n'est pas un réglage de performance : c'est le seul qui décide si le
# backfill dure des jours ou des semaines. Mesuré sur une requête isolée,
# l'attente en file est de **2 h 53** pour 16 min de calcul — la file est
# donc 90 % du temps, et elle se recouvre. Six requêtes en vol transforment
# 304 attentes successives en une cinquantaine d'attentes parallèles.
#
# Six et pas cinquante : Copernicus limite le nombre de travaux simultanés
# par compte, et dépasser cette limite fait refuser les requêtes au lieu de
# les mettre en attente. Six est prudent ; `--parallele` permet de tenter
# plus si le compte le supporte.
PARALLELISME_DEFAUT = 6


def taches_de_telechargement(debut: int, fin: int) -> list[tuple]:
    """Toutes les requêtes CDS d'une période, sans en lancer aucune.

    Séparé du téléchargement pour être vérifiable sans réseau : c'est la
    liste qui décide du volume, et une erreur de bornes ici coûterait des
    heures de file avant d'être vue.

    Les précipitations vont jusqu'à `fin + 1` parce que le cumul d'une
    journée est daté du lendemain : le 31 décembre de la dernière année
    n'est lisible que dans le fichier de l'année suivante.
    """
    taches: list[tuple] = []
    for annee in range(debut, fin + 1):
        for variable, statistique in TEMPERATURES:
            taches.append(("temperature", variable, statistique, annee))
    for annee in range(debut, fin + 2):
        taches.append(("precipitation", None, None, annee))
    return taches


def precharger(taches, cache: Path, parallele: int) -> list[tuple]:
    """Remplit le cache en laissant les attentes se recouvrir.

    Chaque fil porte son propre client : `cdsapi.Client` n'est pas documenté
    comme sûr en parallèle, et le partager ferait dépendre la correction d'un
    détail d'implémentation d'une bibliothèque tierce.

    Une tâche qui échoue n'arrête pas les autres — elle est rendue à
    l'appelant. Perdre cinquante heures de file parce que la trente-septième
    requête a expiré serait le comportement le plus coûteux possible.
    """
    def executer(tache):
        genre, variable, statistique, annee = tache
        client = cdsapi.Client()
        if genre == "temperature":
            return retrieve_year(client, variable, statistique, annee, cache)
        return retrieve_precipitation_year(client, annee, cache)

    echecs: list[tuple] = []
    total = len(taches)
    with ThreadPoolExecutor(max_workers=parallele) as pool:
        en_vol = {pool.submit(executer, t): t for t in taches}
        for termine, futur in enumerate(as_completed(en_vol), start=1):
            tache = en_vol[futur]
            try:
                futur.result()
                print(f"  [{termine}/{total}] {tache[0]} {tache[3]}", flush=True)
            except Exception as erreur:
                echecs.append((tache, erreur))
                print(f"  [{termine}/{total}] ÉCHEC {tache}: {erreur}", flush=True)
    return echecs


def assembler(
    client, debut: int, fin: int, mailles, cache: Path, premier_jour: int, n_jours: int
) -> np.ndarray:
    """Accumulateur (séries, mailles, jours) quantifié pour la période.

    Accumulation quantifiée dès la lecture. Garder du float64 pour
    11 500 mailles × 76 ans demanderait plus de 10 Go de mémoire ; en
    int16 le même tableau tient en 2,5 Go.
    """
    _, lons = grid_axes()
    lignes, colonnes = positions_des_mailles(mailles, len(lons))
    accumulateur = np.full((N_SERIES, len(mailles), n_jours), MISSING, dtype=np.int16)

    for annee in range(debut, fin + 1):
        print(f"année {annee}…", flush=True)
        for index, (jours, valeurs) in enumerate(
            charger_temperatures(client, annee, cache)
        ):
            _deposer(accumulateur, index, jours, valeurs, lignes, colonnes, premier_jour)

    # Les précipitations sont décalées d'un jour : le fichier de l'année N+1
    # est nécessaire pour compléter le 31 décembre de l'année N.
    for annee in range(debut, fin + 2):
        print(f"précipitations {annee}…", flush=True)
        try:
            jours, valeurs = charger_precipitations(client, annee, cache)
        except Exception as erreur:
            # L'année suivant la période demandée peut ne pas exister encore ;
            # il ne manquera alors qu'une seule journée, marquée absente.
            if annee > fin:
                print(f"  ignorée ({erreur})")
                continue
            raise
        _deposer(accumulateur, COLONNE_PLUIE, jours, valeurs, lignes, colonnes,
                 premier_jour)

    return accumulateur


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--from", dest="debut", type=int, default=1950)
    parseur.add_argument("--to", dest="fin", type=int, required=True)
    parseur.add_argument("--out", required=True, type=Path)
    parseur.add_argument("--cache", type=Path, default=Path(".cache"))
    parseur.add_argument(
        "--parallele",
        type=int,
        default=PARALLELISME_DEFAUT,
        help="requêtes CDS simultanées (défaut : %(default)s)",
    )
    args = parseur.parse_args()

    mailles = read_grid_index(args.out / "index" / "grid.bin")
    print(f"{len(mailles)} mailles terrestres à alimenter")

    # Tout télécharger d'abord, en parallèle, puis assembler depuis le cache.
    # L'assemblage relit le cache et ne redemande rien : les deux phases sont
    # séparées pour que l'attente en file — 90 % du temps total — se recouvre
    # au lieu de s'additionner.
    taches = taches_de_telechargement(args.debut, args.fin)
    print(f"{len(taches)} requêtes CDS, {args.parallele} en vol", flush=True)
    echecs = precharger(taches, args.cache, args.parallele)
    if echecs:
        # L'année qui suit la période demandée peut légitimement ne pas
        # exister : son absence ne coûte qu'une journée, marquée absente.
        bloquants = [t for t, _ in echecs if not (t[0] == "precipitation" and t[3] > args.fin)]
        if bloquants:
            raise SystemExit(
                f"{len(bloquants)} requêtes ont échoué ; le cache garde les "
                f"autres, relancer la commande reprend là où elle s'est "
                f"arrêtée.\n" + "\n".join(f"  {t}" for t in bloquants)
            )

    premier_jour = date_to_day(date(args.debut, 1, 1))
    dernier_jour = date_to_day(date(args.fin, 12, 31))
    n_jours = dernier_jour - premier_jour + 1

    accumulateur = assembler(
        cdsapi.Client(), args.debut, args.fin, mailles, args.cache,
        premier_jour, n_jours,
    )

    racine = args.out / "grid"
    for position, maille in enumerate(mailles):
        # Déquantifier puis laisser encode_series requantifier est exact :
        # à échelle identique l'aller-retour int16 → float → int16 ne perd
        # rien, sentinelle comprise.
        series = [
            dequantize(accumulateur[variable, position], DEFAULT_SCALE)
            for variable in range(N_SERIES)
        ]
        dossier = racine / str(maille.cell_id)
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / "history.bin").write_bytes(
            encode_series(maille.cell_id, premier_jour, series)
        )

    write_manifest(
        args.out / "manifest.json",
        format_version=1,
        n_cells=len(mailles),
        n_communes=len(read_commune_index(args.out / "index" / "communes.bin")),
        updated=date.today(),
        first_day=premier_jour,
        last_day=dernier_jour,
    )
    print(f"{len(mailles)} fichiers écrits dans {racine}")


if __name__ == "__main__":
    main()
