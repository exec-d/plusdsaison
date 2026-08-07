#!/usr/bin/env python3
"""Construit l'historique quotidien 1950 → N-1, maille par maille.

Télécharge par année quatre trimestres de température horaire et deux années
de précipitations, en calcule les quatre séries quotidiennes (Tmin, Tmax,
Tmoy, pluie), les assemble en mémoire puis écrit un fichier binaire par maille
terrestre.

Le téléchargement est reprenable : relancer la commande après une
interruption repart des trimestres manquants.

**Un seul backfill à la fois** — `purger_la_file` annule tout travail en
attente sur le compte, sans distinguer le sien de celui d'un autre processus.

    python backfill.py --from 1950 --to 2025 --out .. --cache .cache
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import requests

import cdsapi
import numpy as np
import xarray as xr

from plusdsaison.binary import DEFAULT_SCALE, date_to_day, encode_series
from plusdsaison.cds import (
    PRECIPITATION_DAY_SHIFT,
    PRECIPITATION_FACTOR,
    QUARTERS,
    retrieve_hourly_temperature,
    retrieve_precipitation_year,
)
from plusdsaison.grid import grid_axes
from plusdsaison.index_io import read_commune_index, read_grid_index
from plusdsaison.manifest import write_manifest
from plusdsaison.quantize import MISSING, dequantize, quantize

# L'ordre des colonnes est celui du format publié : Tmin, Tmax, Tmoy, pluie.
# Les trois températures sont calculées d'un même fichier horaire, dans cet
# ordre — voir charger_temperatures.
N_TEMPERATURES = 3
N_SERIES = N_TEMPERATURES + 1
COLONNE_PLUIE = N_TEMPERATURES

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


def _trimestre_a_venir(annee: int, trimestre: int, aujourdhui: date | None = None) -> bool:
    """Le trimestre commence-t-il après aujourd'hui ?

    Le trimestre en cours, lui, est demandé : Copernicus rend les journées
    qu'il a produites et s'arrête là, ce que `daily.py` sait tronquer.
    """
    aujourdhui = aujourdhui or date.today()
    premier_mois = int(QUARTERS[trimestre][0])
    return date(annee, premier_mois, 1) > aujourdhui


def charger_temperatures(
    client, annee: int, cache: Path
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Trois séries (jours, tableau) en degrés Celsius pour l'année.

    Les minima, maxima et moyennes sont calculés ici, à partir de l'horaire,
    et non demandés au dataset dérivé : voir
    [`build_hourly_temperature_request`][] pour la raison — sa file met des
    heures là où celle-ci met des minutes.

    **Les journées sont découpées en heure légale française**, comme le fait
    le dataset dérivé avec `time_zone: utc+01:00`. Sans ce décalage les minima
    nocturnes glissent d'une heure et le comptage des jours de gel s'en trouve
    faussé.

    Le décalage prive le 1er janvier de son heure de minuit, restée dans le
    fichier du quatrième trimestre de l'année précédente. Celui-ci est relu
    quand il est en cache — c'est le cas de toutes les années sauf la
    première de l'archive, dont le 1er janvier est donc calculé sur 23 heures.
    Écart mesuré dans ce cas : 0,28 K au pire.
    """
    morceaux = []

    precedent = cache / f"2m_temperature_hourly_{annee - 1}_T4.nc"
    if precedent.exists() and precedent.stat().st_size > 0:
        morceaux.append(xr.open_dataset(precedent).isel(valid_time=slice(-1, None)))

    for trimestre in sorted(QUARTERS):
        if _trimestre_a_venir(annee, trimestre):
            # Un trimestre qui n'a pas commencé n'existe chez Copernicus sous
            # aucune forme : le demander est une erreur, pas une absence. Sans
            # ce filtre, le rafraîchissement quotidien échouerait tous les
            # jours d'octobre à décembre — au moment précis où l'année en cours
            # est la plus intéressante.
            continue
        chemin = retrieve_hourly_temperature(client, annee, trimestre, cache)
        morceaux.append(xr.open_dataset(chemin))

    if not morceaux:
        raise ValueError(f"aucun trimestre disponible pour {annee}")

    horaire = xr.concat(morceaux, dim="valid_time")
    nom = list(horaire.data_vars)[0]

    # Le décalage est appliqué à l'axe, pas aux valeurs : grouper ensuite par
    # date suffit à découper les journées à la bonne frontière.
    decale = horaire.assign_coords(
        valid_time=horaire.valid_time + np.timedelta64(1, "h")
    )
    groupes = decale[nom].groupby("valid_time.date")

    series = []
    for agregat in (groupes.min(), groupes.max(), groupes.mean()):
        dates = np.asarray(agregat["date"].values, dtype="datetime64[D]")
        jours = (dates - np.datetime64("1950-01-01", "D")).astype(np.int64)
        valeurs = agregat.values.astype(np.float64) - KELVIN_OFFSET
        series.append((jours, valeurs))
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
        for trimestre in sorted(QUARTERS):
            taches.append(("temperature", None, trimestre, annee))
    for annee in range(debut, fin + 2):
        taches.append(("precipitation", None, None, annee))
    return taches


#: Où lister et annuler les travaux du compte.
API_TRAVAUX = "https://cds.climate.copernicus.eu/api/retrieve/v1/jobs"


def _cle_cds() -> str | None:
    """La clé lue dans ~/.cdsapirc, ou None si le fichier n'existe pas."""
    fichier = Path.home() / ".cdsapirc"
    if not fichier.exists():
        return None
    for ligne in fichier.read_text().splitlines():
        if ligne.startswith("key:"):
            return ligne.split(":", 1)[1].strip()
    return None


def purger_la_file() -> int:
    """Annule les travaux restés en attente d'un run précédent.

    **Tuer le client n'annule rien côté serveur.** Un backfill interrompu
    laisse ses requêtes en file, où elles continuent d'occuper les créneaux de
    concurrence du compte. Mesuré : quatre travaux orphelins d'un run abandonné
    ont bloqué trois heures durant les cinq requêtes du run suivant, sans
    qu'aucun journal local ne le montre — le client attendait poliment des
    travaux que le serveur ne démarrerait jamais faute de créneau.

    Relancer sans purger aggrave le mal : `cdsapi` soumet une nouvelle requête
    plutôt que de reprendre l'ancienne, et chaque redémarrage double la file.

    Purger au démarrage plutôt qu'à l'interruption, parce qu'un `kill -9` ne se
    rattrape pas : à cet instant précis, aucun travail de *ce* processus n'a
    encore été soumis, donc tout ce qui attend vient forcément d'un run mort.

    Sans clé lisible, on ne purge rien et on le dit : ce nettoyage est un
    confort, pas une étape dont dépend la correction.
    """
    cle = _cle_cds()
    if cle is None:
        print("~/.cdsapirc illisible : file non purgée", flush=True)
        return 0

    entetes = {"PRIVATE-TOKEN": cle}
    try:
        reponse = requests.get(f"{API_TRAVAUX}?limit=200", headers=entetes, timeout=30)
        reponse.raise_for_status()
        travaux = reponse.json().get("jobs", [])
    except Exception as erreur:
        print(f"file non purgée ({erreur})", flush=True)
        return 0

    orphelins = [t["jobID"] for t in travaux if t.get("status") == "accepted"]
    annules = 0
    for identifiant in orphelins:
        try:
            r = requests.delete(f"{API_TRAVAUX}/{identifiant}", headers=entetes, timeout=30)
            if r.status_code == 200:
                annules += 1
        except Exception:
            pass
    return annules


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
        genre, _, trimestre, annee = tache
        client = cdsapi.Client()
        if genre == "temperature":
            return retrieve_hourly_temperature(client, annee, trimestre, cache)
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
    orphelins = purger_la_file()
    if orphelins:
        print(f"{orphelins} travaux d'un run précédent annulés", flush=True)

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
