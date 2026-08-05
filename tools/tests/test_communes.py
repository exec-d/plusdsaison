"""Le référentiel des communes est construit une fois puis figé dans
l'index. Ces tests substituent les API HTTP : ils vérifient le parsing et
le découpage en lots, pas la disponibilité des services."""

import numpy as np
import pytest

from plusdsaison.communes import (
    MAX_DISTANCE_KM,
    Commune,
    attach_to_land_cells,
    fetch_communes,
    fetch_elevations,
    haversine_km,
)
from plusdsaison.grid import cell_id


class FauxRetour:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FausseSession:
    """Rejoue des réponses préparées et enregistre les paramètres reçus."""

    def __init__(self, reponses):
        self._reponses = list(reponses)
        self.appels = []

    def get(self, url, params=None, timeout=None):
        self.appels.append((url, params))
        return FauxRetour(self._reponses.pop(0))


def test_les_communes_sont_lues_avec_leur_centre():
    session = FausseSession([[
        {
            "code": "01443",
            "nom": "Villars-les-Dombes",
            "codeDepartement": "01",
            "centre": {"type": "Point", "coordinates": [5.0308, 46.0022]},
        }
    ]])

    communes = fetch_communes(session)

    assert len(communes) == 1
    assert communes[0].insee == "01443"
    assert communes[0].nom == "Villars-les-Dombes"
    assert communes[0].departement == "01"
    # GeoJSON ordonne en [longitude, latitude] : les inverser décalerait
    # toutes les communes de France.
    assert communes[0].lon == pytest.approx(5.0308)
    assert communes[0].lat == pytest.approx(46.0022)


def test_une_commune_sans_centre_est_ignoree():
    session = FausseSession([[
        {"code": "97501", "nom": "Sans centre", "codeDepartement": "975"}
    ]])
    assert fetch_communes(session) == []


def test_les_altitudes_sont_demandees_par_lots():
    communes = [
        Commune(insee=str(i), nom=f"C{i}", departement="01", lat=46.0, lon=5.0)
        for i in range(5)
    ]
    session = FausseSession([
        {"elevation": [100.0, 200.0]},
        {"elevation": [300.0, 400.0]},
        {"elevation": [500.0]},
    ])

    fetch_elevations(session, communes, batch=2)

    assert [c.altitude for c in communes] == [100.0, 200.0, 300.0, 400.0, 500.0]
    assert len(session.appels) == 3
    # Les coordonnées partent groupées, séparées par des virgules.
    assert session.appels[0][1]["latitude"].count(",") == 1


def test_un_lot_de_taille_inattendue_est_rejete():
    communes = [
        Commune(insee="1", nom="A", departement="01", lat=46.0, lon=5.0),
        Commune(insee="2", nom="B", departement="01", lat=46.1, lon=5.1),
    ]
    session = FausseSession([{"elevation": [100.0]}])

    with pytest.raises(ValueError, match="altitudes"):
        fetch_elevations(session, communes, batch=2)


def test_haversine_sur_une_distance_connue():
    # Paris (48.8566, 2.3522) → Lyon (45.7640, 4.8357) : ~392 km.
    assert haversine_km(48.8566, 2.3522, 45.7640, 4.8357) == pytest.approx(392, abs=5)


def test_distance_nulle_sur_le_meme_point():
    assert haversine_km(46.0, 5.0, 46.0, 5.0) == pytest.approx(0.0, abs=1e-9)


def test_rattachement_a_la_maille_la_plus_proche():
    lats = np.array([46.1, 46.0])
    lons = np.array([5.0, 5.1])
    mask = np.array([[True, True], [True, True]])
    commune = Commune(insee="1", nom="A", departement="01", lat=46.01, lon=5.02)

    attach_to_land_cells([commune], lats, lons, mask)

    # (46.0, 5.0) est la plus proche : ligne 1, colonne 0.
    assert commune.cell_id == cell_id(1, 0, len(lons))
    assert commune.distance_km < 5


def test_une_maille_marine_est_ecartee_au_profit_de_la_terre():
    lats = np.array([46.1, 46.0])
    lons = np.array([5.0, 5.1])
    # La maille géographiquement la plus proche est marine : le rattachement
    # doit sauter à la maille terrestre suivante plutôt que de rendre un
    # point sans données.
    mask = np.array([[True, True], [False, True]])
    commune = Commune(insee="1", nom="A", departement="01", lat=46.01, lon=5.02)

    attach_to_land_cells([commune], lats, lons, mask)

    assert commune.cell_id != cell_id(1, 0, len(lons))
    assert commune.cell_id in {
        cell_id(0, 0, len(lons)), cell_id(0, 1, len(lons)), cell_id(1, 1, len(lons))
    }


def test_la_distance_de_rattachement_est_renseignee():
    lats = np.array([46.0])
    lons = np.array([5.0])
    mask = np.array([[True]])
    commune = Commune(insee="1", nom="A", departement="01", lat=47.0, lon=5.0)

    attach_to_land_cells([commune], lats, lons, mask)

    # 1° de latitude ≈ 111 km : bien au-delà du seuil d'alerte, ce que
    # l'application doit pouvoir signaler.
    assert commune.distance_km == pytest.approx(111, abs=3)
    assert commune.distance_km > MAX_DISTANCE_KM


def test_aucune_maille_terrestre_est_une_erreur():
    lats = np.array([46.0])
    lons = np.array([5.0])
    mask = np.array([[False]])
    commune = Commune(insee="1", nom="A", departement="01", lat=46.0, lon=5.0)

    with pytest.raises(ValueError, match="aucune maille terrestre"):
        attach_to_land_cells([commune], lats, lons, mask)
