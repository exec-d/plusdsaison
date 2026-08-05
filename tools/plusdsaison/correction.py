"""Correction altitudinale des températures par gradient adiabatique.

Une maille ERA5-Land de 9 km porte une orographie moyennée qui peut s'écarter
de plusieurs centaines de mètres de l'altitude réelle d'un point. À 0,0065 °C
par mètre, 600 m d'écart valent près de 4 °C — de quoi rendre l'ensemble de
l'arc alpin faux si on l'ignore.

Ce module ne sert ici qu'à la validation contre les stations Météo-France.
La correction destinée aux utilisateurs est appliquée par l'application, qui
seule connaît l'altitude de la commune consultée : corriger en amont rendrait
le fichier d'une maille juste pour une commune et faux pour sa voisine.
"""

import numpy as np
from numpy.typing import ArrayLike

LAPSE_RATE_C_PER_M = 0.0065


def correct_temperature(
    temps: ArrayLike, target_altitude_m: float, model_orography_m: float
) -> np.ndarray:
    """Ramène des températures de l'altitude du modèle à celle de la cible."""
    valeurs = np.asarray(temps, dtype=np.float64)
    ecart_m = target_altitude_m - model_orography_m
    return valeurs - LAPSE_RATE_C_PER_M * ecart_m
