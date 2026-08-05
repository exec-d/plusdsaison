"""Conversion des mesures flottantes en entiers signés 16 bits, et retour.

Deux octets par valeur au lieu de quatre, sans perte utile : températures et
précipitations sont significatives au dixième d'unité. La valeur -32768 est
réservée aux mesures absentes, ce qui impose de borner les valeurs réelles à
-32767 pour qu'aucune ne puisse être relue comme manquante.
"""

import numpy as np
from numpy.typing import ArrayLike

MISSING = -32768
INT16_MIN = -32767
INT16_MAX = 32767


def quantize(values: ArrayLike, scale: int) -> np.ndarray:
    """Convertit des flottants en entiers 16 bits multipliés par `scale`.

    Les valeurs non finies (NaN, ±inf) deviennent `MISSING`.
    """
    arr = np.asarray(values, dtype=np.float64)
    out = np.full(arr.shape, MISSING, dtype=np.int16)
    valide = np.isfinite(arr)
    if valide.any():
        echelonnees = np.rint(arr[valide] * scale)
        np.clip(echelonnees, INT16_MIN, INT16_MAX, out=echelonnees)
        out[valide] = echelonnees.astype(np.int16)
    return out


def dequantize(codes: ArrayLike, scale: int) -> np.ndarray:
    """Reconstitue les flottants ; `MISSING` redevient NaN."""
    arr = np.asarray(codes, dtype=np.int16)
    out = arr.astype(np.float64) / scale
    out[arr == MISSING] = np.nan
    return out
