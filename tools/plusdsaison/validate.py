"""Comparaison de nos mailles aux stations Météo-France.

C'est le garde-fou du pipeline : une erreur de conversion d'unité, un
décalage de fuseau ou un rattachement inversé se voient immédiatement sur le
biais, alors qu'ils passeraient tous les tests unitaires. Le seuil bloque la
publication au lieu de se contenter d'avertir.
"""

from dataclasses import dataclass

import numpy as np

BIAS_THRESHOLD_C = 1.5


@dataclass(frozen=True)
class Station:
    id: str
    nom: str
    lat: float
    lon: float
    altitude: float


def compare_series(modele: np.ndarray, station: np.ndarray) -> dict:
    """Biais et RMSE sur les seuls jours renseignés des deux côtés."""
    a = np.asarray(modele, dtype=np.float64)
    b = np.asarray(station, dtype=np.float64)
    commun = np.isfinite(a) & np.isfinite(b)
    effectif = int(commun.sum())

    if effectif == 0:
        return {"bias": float("nan"), "rmse": float("nan"), "n": 0}

    ecarts = a[commun] - b[commun]
    return {
        "bias": float(np.mean(ecarts)),
        "rmse": float(np.sqrt(np.mean(ecarts**2))),
        "n": effectif,
    }


def departement_verdict(resultats: list[dict]) -> dict:
    """Agrège les stations d'un département et tranche sur le seuil."""
    exploitables = [r for r in resultats if r["n"] > 0]

    if not exploitables:
        return {
            "ok": False,
            "bias": float("nan"),
            "rmse": float("nan"),
            "n_stations": 0,
            "raison": "aucune station exploitable",
        }

    biais = float(np.mean([r["bias"] for r in exploitables]))
    rmse = float(np.mean([r["rmse"] for r in exploitables]))
    return {
        "ok": abs(biais) <= BIAS_THRESHOLD_C,
        "bias": biais,
        "rmse": rmse,
        "n_stations": len(exploitables),
    }


def verdict_line(departement: str, verdict: dict) -> str:
    """Ligne de journal d'un verdict départemental.

    Un département sans station n'est pas un département hors tolérance :
    l'un dit que la comparaison n'a pas eu lieu, l'autre que les données sont
    fausses, et les deux appellent des gestes opposés — corriger la question
    posée, ou corriger les données. Confondus, ils ont fait lire « biais
    +nan °C — HORS TOLÉRANCE » pendant six jours à un département dont
    Météo-France ne publiait rien sous le code demandé.
    """
    if verdict["n_stations"] == 0:
        return (
            f"département {departement} : "
            f"{verdict['raison']} — comparaison impossible"
        )

    stations = "station" if verdict["n_stations"] == 1 else "stations"
    etat = "OK" if verdict["ok"] else "HORS TOLÉRANCE"
    return (
        f"département {departement} : biais {verdict['bias']:+.2f} °C "
        f"sur {verdict['n_stations']} {stations} — {etat}"
    )
