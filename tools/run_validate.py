#!/usr/bin/env python3
"""Compare nos mailles aux stations Météo-France et publie l'écart.

Sort en code 1 si un département dépasse la tolérance : le workflow échoue
alors sans rien publier, plutôt que de diffuser des données dégradées.

    python run_validate.py --out .. --dept 01 --dept 29 --dept 74
"""

import argparse
import csv
import gzip
import io
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import requests
from scipy.spatial import cKDTree

from plusdsaison.binary import date_to_day, decode_series
from plusdsaison.correction import correct_temperature
from plusdsaison.index_io import read_grid_index
from plusdsaison.validate import Station, compare_series, departement_verdict

# Jeu « Données climatologiques de base - quotidiennes » de Météo-France,
# diffusé par département sur data.gouv.fr sous Licence Ouverte 2.0.
MF_DATASET = "donnees-climatologiques-de-base-quotidiennes"
MF_API = f"https://www.data.gouv.fr/api/1/datasets/{MF_DATASET}/"

# Colonne de l'historique comparée aux stations : la moyenne quotidienne.
# Le seuil de la spec porte sur elle, et c'est la moins sensible aux écarts
# d'horaire de relevé entre le modèle et l'abri.
COLONNE_TMOY = 2
CHAMP_TMOY = "TM"

# Une station trop loin de sa maille ne dit plus rien de la maille.
DISTANCE_MAX_KM = 15.0

# Le CSV Météo-France est en Latin-1 sur les millésimes anciens et en UTF-8
# sur les récents : tenter l'un puis l'autre plutôt que d'imposer.
ENCODAGES = ("utf-8", "latin-1")


def ressources_du_departement(session, departement: str) -> list[dict]:
    """Ressources CSV du département, période récente uniquement."""
    reponse = session.get(MF_API, timeout=120)
    reponse.raise_for_status()
    marqueur = f"QUOT_departement_{departement}_periode_"
    return [
        r for r in reponse.json()["resources"]
        if r.get("title", "").startswith(marqueur) and "RR-T-Vent" in r.get("title", "")
    ]


def _decoder(brut: bytes) -> str:
    for encodage in ENCODAGES:
        try:
            return brut.decode(encodage)
        except UnicodeDecodeError:
            continue
    return brut.decode("latin-1", errors="replace")


def _lire_csv(session, url: str) -> list[dict]:
    """Télécharge et décompresse un CSV quotidien de Météo-France."""
    reponse = session.get(url, timeout=300)
    reponse.raise_for_status()
    contenu = reponse.content
    if url.endswith(".gz"):
        contenu = gzip.decompress(contenu)
    # Le séparateur est le point-virgule, et les valeurs sont déjà en
    # degrés décimaux malgré ce qu'annonce le descriptif (« en °C et 1/10 »).
    return list(csv.DictReader(io.StringIO(_decoder(contenu)), delimiter=";"))


def _flottant(valeur: str) -> float:
    """Une case vide vaut absence, pas zéro."""
    valeur = (valeur or "").strip()
    if not valeur:
        return float("nan")
    try:
        return float(valeur)
    except ValueError:
        return float("nan")


def _serie_de_la_maille(racine: Path, cell_id: int) -> tuple[int, np.ndarray] | None:
    """Série de température moyenne d'une maille, historique puis année en cours.

    L'historique s'arrête à l'année close ; les journées récentes vivent dans
    `current/`. Les concaténer permet de valider sur la période que
    Météo-France publie effectivement.
    """
    chemin_histoire = racine / "grid" / str(cell_id) / "history.bin"
    chemin_courant = racine / "current" / f"{cell_id}.bin"

    if not chemin_histoire.exists():
        return None

    entete, colonnes = decode_series(chemin_histoire.read_bytes())
    debut = entete.start_day
    serie = colonnes[COLONNE_TMOY]

    if chemin_courant.exists():
        entete_c, colonnes_c = decode_series(chemin_courant.read_bytes())
        trou = entete_c.start_day - (debut + len(serie))
        if trou >= 0:
            serie = np.concatenate([
                serie,
                np.full(trou, np.nan),
                colonnes_c[COLONNE_TMOY],
            ])

    return debut, serie


def _comparer_ressource(session, ressource: dict, mailles: dict, arbre, ids, racine: Path):
    """Compare chaque station d'un CSV à la maille qui la porte."""
    lignes = _lire_csv(session, ressource["url"])

    par_station: dict[str, list[dict]] = defaultdict(list)
    for ligne in lignes:
        par_station[ligne["NUM_POSTE"]].append(ligne)

    resultats = []
    for numero, releves in par_station.items():
        tete = releves[0]
        lat, lon = _flottant(tete["LAT"]), _flottant(tete["LON"])
        altitude = _flottant(tete["ALTI"])
        if not (np.isfinite(lat) and np.isfinite(lon) and np.isfinite(altitude)):
            continue

        station = Station(
            id=numero, nom=tete.get("NOM_USUEL", ""), lat=lat, lon=lon, altitude=altitude
        )

        _, index = arbre.query([[station.lat, station.lon]], k=1)
        maille = mailles[ids[int(np.atleast_1d(index)[0])]]
        if _haversine_grossier(station, maille) > DISTANCE_MAX_KM:
            continue

        lu = _serie_de_la_maille(racine, maille.cell_id)
        if lu is None:
            continue
        debut, modele = lu

        # Ramener le modèle à l'altitude de l'abri : sans cette correction,
        # une station de montagne accuserait plusieurs degrés d'écart qui ne
        # diraient rien de la qualité de la maille.
        modele = correct_temperature(modele, station.altitude, maille.orography_m)

        observe = np.full(len(modele), np.nan)
        for releve in releves:
            texte = releve["AAAAMMJJ"].strip()
            if len(texte) != 8:
                continue
            jour = date_to_day(
                date(int(texte[:4]), int(texte[4:6]), int(texte[6:8]))
            ) - debut
            if 0 <= jour < len(observe):
                observe[jour] = _flottant(releve.get(CHAMP_TMOY, ""))

        resultats.append(compare_series(modele, observe))

    return resultats


def _haversine_grossier(station: Station, maille) -> float:
    """Distance station-maille, en kilomètres."""
    from plusdsaison.communes import haversine_km

    return haversine_km(station.lat, station.lon, maille.lat, maille.lon)


def main() -> None:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--out", required=True, type=Path)
    parseur.add_argument("--dept", action="append", required=True,
                         help="code département, répétable")
    args = parseur.parse_args()

    liste = read_grid_index(args.out / "index" / "grid.bin")
    mailles = {m.cell_id: m for m in liste}
    ids = [m.cell_id for m in liste]
    arbre = cKDTree([[m.lat, m.lon] for m in liste])

    session = requests.Session()

    dossier = args.out / "validation"
    dossier.mkdir(parents=True, exist_ok=True)

    echecs = []
    for departement in args.dept:
        resultats = []
        for ressource in ressources_du_departement(session, departement):
            resultats.extend(
                _comparer_ressource(session, ressource, mailles, arbre, ids, args.out)
            )

        verdict = departement_verdict(resultats)
        (dossier / f"{departement}.json").write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False) + "\n"
        )
        etat = "OK" if verdict["ok"] else "HORS TOLÉRANCE"
        print(f"département {departement} : biais {verdict['bias']:+.2f} °C — {etat}")
        if not verdict["ok"]:
            echecs.append(departement)

    if echecs:
        raise SystemExit(
            f"validation en échec sur : {', '.join(echecs)} — publication interrompue"
        )


if __name__ == "__main__":
    main()
