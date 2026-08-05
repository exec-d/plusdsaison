"""Le référentiel des communes est construit une fois puis figé dans
l'index. Ces tests substituent les API HTTP : ils vérifient le parsing et
le découpage en lots, pas la disponibilité des services."""

import pytest

from plusdsaison.communes import Commune, fetch_communes, fetch_elevations


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
