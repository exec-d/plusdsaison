"""Contrat du format .bin publié. L'application Dart lit exactement ces
octets : tout changement ici est une rupture pour les clients installés."""

from datetime import date

import numpy as np
import pytest

from plusdsaison.binary import (
    EPOCH,
    HEADER_SIZE,
    MAGIC,
    date_to_day,
    day_to_date,
    decode_series,
    encode_series,
)


def _colonnes():
    return [
        np.array([-2.4, 0.0, 3.1]),      # Tmin
        np.array([7.8, 9.9, 12.0]),      # Tmax
        np.array([2.7, 4.9, 7.5]),       # Tmoy
        np.array([0.0, 12.4, np.nan]),   # Pluie
    ]


def test_en_tete_fait_dix_neuf_octets():
    assert HEADER_SIZE == 19


def test_aller_retour_restitue_les_colonnes():
    blob = encode_series(cell_id=4242, start_day=100, columns=_colonnes())
    entete, colonnes = decode_series(blob)

    assert entete.cell_id == 4242
    assert entete.start_day == 100
    assert entete.n_days == 3
    assert entete.n_vars == 4
    assert entete.scale == 10
    assert colonnes[0] == pytest.approx([-2.4, 0.0, 3.1], abs=0.05)
    assert colonnes[3][:2] == pytest.approx([0.0, 12.4], abs=0.05)
    assert np.isnan(colonnes[3][2])


def test_taille_totale_previsible():
    blob = encode_series(cell_id=1, start_day=0, columns=_colonnes())
    assert len(blob) == HEADER_SIZE + 4 * 3 * 2


def test_commence_par_la_signature():
    assert encode_series(1, 0, _colonnes())[:4] == MAGIC


def test_signature_invalide_rejetee():
    blob = bytearray(encode_series(1, 0, _colonnes()))
    blob[:4] = b"XXXX"
    with pytest.raises(ValueError, match="signature"):
        decode_series(bytes(blob))


def test_version_inconnue_rejetee():
    blob = bytearray(encode_series(1, 0, _colonnes()))
    blob[4] = 99
    with pytest.raises(ValueError, match="version"):
        decode_series(bytes(blob))


def test_corps_tronque_rejete():
    blob = encode_series(1, 0, _colonnes())
    with pytest.raises(ValueError, match="taille"):
        decode_series(blob[:-2])


def test_colonnes_de_longueurs_differentes_rejetees():
    with pytest.raises(ValueError, match="même nombre de jours"):
        encode_series(1, 0, [np.array([1.0, 2.0]), np.array([1.0])])


def test_aucune_colonne_rejetee():
    with pytest.raises(ValueError, match="au moins une variable"):
        encode_series(1, 0, [])


def test_conversion_de_dates():
    assert date_to_day(EPOCH) == 0
    assert date_to_day(date(1950, 1, 2)) == 1
    assert day_to_date(0) == EPOCH
    assert day_to_date(date_to_day(date(2026, 8, 4))) == date(2026, 8, 4)
