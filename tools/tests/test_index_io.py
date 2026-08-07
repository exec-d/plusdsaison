"""L'index est lu par l'application Dart depuis un asset embarqué : son
format est un contrat aussi strict que celui des séries."""

import pytest

from plusdsaison.communes import Commune
from plusdsaison.index_io import (
    GridCell,
    read_commune_index,
    read_grid_index,
    write_commune_index,
    write_grid_index,
)


def test_aller_retour_de_la_grille(tmp_path):
    chemin = tmp_path / "grid.bin"
    mailles = [
        GridCell(cell_id=0, lat=51.5, lon=-5.5, orography_m=12.0),
        GridCell(cell_id=1234, lat=46.0, lon=5.0, orography_m=287.0),
    ]

    write_grid_index(chemin, mailles)
    relues = read_grid_index(chemin)

    assert len(relues) == 2
    assert relues[1].cell_id == 1234
    assert relues[1].lat == pytest.approx(46.0, abs=1e-4)
    assert relues[1].lon == pytest.approx(5.0, abs=1e-4)
    assert relues[1].orography_m == pytest.approx(287.0, abs=0.5)


def test_orographie_negative_supportee(tmp_path):
    # Les polders et dépressions descendent sous le niveau de la mer.
    chemin = tmp_path / "grid.bin"
    write_grid_index(chemin, [GridCell(cell_id=1, lat=43.5, lon=4.5, orography_m=-3.0)])
    assert read_grid_index(chemin)[0].orography_m == pytest.approx(-3.0, abs=0.5)


def test_aller_retour_des_communes(tmp_path):
    chemin = tmp_path / "communes.bin"
    communes = [
        Commune(
            insee="01443", nom="Villars-les-Dombes", departement="01",
            lat=46.0022, lon=5.0308, altitude=281.0, cell_id=1234, distance_km=3.42,
        ),
        Commune(
            insee="74056", nom="Chamonix-Mont-Blanc", departement="74",
            lat=45.9237, lon=6.8694, altitude=1041.0, cell_id=999, distance_km=1.05,
        ),
    ]

    write_commune_index(chemin, communes)
    relues = read_commune_index(chemin)

    assert len(relues) == 2
    assert relues[0].insee == "01443"
    assert relues[0].nom == "Villars-les-Dombes"
    assert relues[0].cell_id == 1234
    assert relues[0].altitude == pytest.approx(281.0, abs=0.5)
    assert relues[0].distance_km == pytest.approx(3.42, abs=0.01)
    assert relues[1].nom == "Chamonix-Mont-Blanc"


def test_les_accents_survivent_a_l_aller_retour(tmp_path):
    chemin = tmp_path / "communes.bin"
    write_commune_index(chemin, [
        Commune(insee="29019", nom="Brest", departement="29", lat=48.39, lon=-4.48,
                altitude=55.0, cell_id=1, distance_km=2.0),
        Commune(insee="2A004", nom="Ajaccio", departement="2A", lat=41.92, lon=8.73,
                altitude=9.0, cell_id=2, distance_km=1.0),
        Commune(insee="63113", nom="Clermont-Ferrand", departement="63", lat=45.77,
                lon=3.08, altitude=396.0, cell_id=3, distance_km=1.5),
        Commune(insee="21231", nom="Dijon", departement="21", lat=47.32, lon=5.04,
                altitude=245.0, cell_id=4, distance_km=1.5),
        Commune(insee="88475", nom="Xertigny", departement="88", lat=48.05, lon=6.40,
                altitude=380.0, cell_id=5, distance_km=1.0),
    ])
    relues = read_commune_index(chemin)
    assert [c.nom for c in relues] == [
        "Brest", "Ajaccio", "Clermont-Ferrand", "Dijon", "Xertigny",
    ]
    # Les codes INSEE corses commencent par une lettre : le champ doit rester
    # une chaîne, jamais un entier.
    assert relues[1].insee == "2A004"


def test_une_commune_sans_rattachement_est_refusee(tmp_path):
    chemin = tmp_path / "communes.bin"
    incomplete = Commune(insee="1", nom="A", departement="01", lat=46.0, lon=5.0)
    with pytest.raises(ValueError, match="rattachée"):
        write_commune_index(chemin, [incomplete])


def test_les_codes_postaux_survivent_a_l_aller_retour(tmp_path):
    chemin = tmp_path / "communes.bin"
    write_commune_index(chemin, [
        # Aucun code : le cas existe, et ne doit pas décaler la suite.
        Commune(insee="99999", nom="Sans code", departement="99", lat=46.0, lon=5.0,
                altitude=100.0, cell_id=1, distance_km=1.0),
        # Un seul, avec un zéro de tête — celui qu'un entier perdrait.
        Commune(insee="01443", nom="Villars-les-Dombes", departement="01",
                lat=46.0022, lon=5.0308, altitude=281.0, cell_id=2,
                distance_km=3.42, codes_postaux=["01330"]),
        # Plusieurs : la relation n'est bijective dans aucun sens.
        Commune(insee="75056", nom="Paris", departement="75", lat=48.85, lon=2.35,
                altitude=35.0, cell_id=3, distance_km=1.0,
                codes_postaux=["75001", "75002", "75116"]),
    ])
    relues = read_commune_index(chemin)

    assert relues[0].codes_postaux == []
    assert relues[1].codes_postaux == ["01330"]
    assert relues[2].codes_postaux == ["75001", "75002", "75116"]
    # Le nom qui suit une entrée à codes multiples se relit intact : c'est ce
    # qui prouve que le décalage a été repris correctement.
    assert relues[2].nom == "Paris"


def test_un_code_postal_non_conforme_est_refuse(tmp_path):
    chemin = tmp_path / "communes.bin"
    # Quatre chiffres au lieu de cinq : accepté, le champ suivant serait lu un
    # octet trop tôt et tout le reste du fichier partirait de travers.
    boiteuse = Commune(insee="01443", nom="Villars", departement="01", lat=46.0,
                       lon=5.0, altitude=281.0, cell_id=1, distance_km=1.0,
                       codes_postaux=["1330"])
    with pytest.raises(ValueError, match="code postal invalide"):
        write_commune_index(chemin, [boiteuse])
