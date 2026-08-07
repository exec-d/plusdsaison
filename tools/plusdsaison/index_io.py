"""Index publié : la grille et le référentiel des communes.

`communes.bin` est embarqué comme asset dans l'application, donc lu par du
code Dart : ces formats sont des contrats binaires. Les coordonnées sont
stockées en entiers au dix-millième de degré (~11 m de précision), les
altitudes au mètre, les distances de rattachement au décamètre.

Une entrée de `communes.bin` (version 2) :

    5s  code INSEE, ASCII
    I   identifiant de maille
    i   latitude × 10 000
    i   longitude × 10 000
    h   altitude, en mètres
    H   distance au centre de la maille × 100
    B   longueur du nom, en octets
    …   le nom, en UTF-8
    B   nombre de codes postaux            ← version 2
    …   les codes postaux, 5 octets ASCII chacun   ← version 2

Le nom et les codes postaux étant de longueur variable, aucune entrée n'est
adressable directement : le fichier se lit d'une traite.
"""

import struct
from dataclasses import dataclass
from pathlib import Path

from .communes import Commune

GRID_MAGIC = b"PDSG"
COMMUNE_MAGIC = b"PDSC"

#: Deux fichiers, deux formats, deux versions.
#:
#: Une constante partagée obligeait à réécrire la grille — qui n'a pas changé
#: depuis le premier jour et ne changera plus, l'emprise ERA5-Land étant
#: fixée — chaque fois que le référentiel communal évoluait.
GRID_VERSION = 1
COMMUNE_VERSION = 2

_GRID_HEADER = "<4sBI"
_GRID_CELL = "<Iiih"
_COMMUNE_HEADER = "<4sBI"
_COMMUNE_FIXED = "<5sIiihH"

#: Un code postal fait cinq chiffres, stockés en ASCII comme le code INSEE.
#:
#: En entier, 01330 s'écrirait 1330 et se relirait faux dans les neuf premiers
#: départements. Quatre octets gagnés ne valent pas un zéro de tête perdu.
POSTAL_CODE_BYTES = 5

#: Autant de codes postaux qu'une entrée peut porter.
#:
#: Le compte tient sur un octet ; Paris, le maximum observé, en a vingt.
MAX_POSTAL_CODES = 255

COORD_SCALE = 10_000
DISTANCE_SCALE = 100


@dataclass(frozen=True)
class GridCell:
    cell_id: int
    lat: float
    lon: float
    orography_m: float


def write_grid_index(path: Path, cells: list[GridCell]) -> None:
    morceaux = [struct.pack(_GRID_HEADER, GRID_MAGIC, GRID_VERSION, len(cells))]
    for maille in cells:
        morceaux.append(
            struct.pack(
                _GRID_CELL,
                maille.cell_id,
                round(maille.lat * COORD_SCALE),
                round(maille.lon * COORD_SCALE),
                round(maille.orography_m),
            )
        )
    Path(path).write_bytes(b"".join(morceaux))


def read_grid_index(path: Path) -> list[GridCell]:
    blob = Path(path).read_bytes()
    magic, version, n_cells = struct.unpack_from(_GRID_HEADER, blob)
    if magic != GRID_MAGIC:
        raise ValueError(f"signature inattendue : {magic!r}")
    if version != GRID_VERSION:
        raise ValueError(f"version de format non gérée : {version}")

    taille = struct.calcsize(_GRID_CELL)
    decalage = struct.calcsize(_GRID_HEADER)
    mailles = []
    for _ in range(n_cells):
        cell_id, lat, lon, orographie = struct.unpack_from(_GRID_CELL, blob, decalage)
        mailles.append(
            GridCell(
                cell_id=cell_id,
                lat=lat / COORD_SCALE,
                lon=lon / COORD_SCALE,
                orography_m=float(orographie),
            )
        )
        decalage += taille
    return mailles


def write_commune_index(path: Path, communes: list[Commune]) -> None:
    morceaux = [
        struct.pack(_COMMUNE_HEADER, COMMUNE_MAGIC, COMMUNE_VERSION, len(communes))
    ]
    for commune in communes:
        if commune.cell_id is None or commune.distance_km is None:
            raise ValueError(f"commune {commune.insee} non rattachée à une maille")
        if commune.altitude is None:
            raise ValueError(f"commune {commune.insee} sans altitude")

        nom = commune.nom.encode("utf-8")
        if len(nom) > 255:
            raise ValueError(f"nom trop long pour {commune.insee}")

        morceaux.append(
            struct.pack(
                _COMMUNE_FIXED,
                commune.insee.encode("ascii"),
                commune.cell_id,
                round(commune.lat * COORD_SCALE),
                round(commune.lon * COORD_SCALE),
                round(commune.altitude),
                min(round(commune.distance_km * DISTANCE_SCALE), 65535),
            )
        )
        morceaux.append(struct.pack("<B", len(nom)))
        morceaux.append(nom)

        codes = commune.codes_postaux[:MAX_POSTAL_CODES]
        morceaux.append(struct.pack("<B", len(codes)))
        for code in codes:
            if len(code) != POSTAL_CODE_BYTES or not code.isdigit():
                raise ValueError(
                    f"code postal invalide pour {commune.insee} : {code!r}"
                )
            morceaux.append(code.encode("ascii"))
    Path(path).write_bytes(b"".join(morceaux))


def read_commune_index(path: Path) -> list[Commune]:
    blob = Path(path).read_bytes()
    magic, version, n_communes = struct.unpack_from(_COMMUNE_HEADER, blob)
    if magic != COMMUNE_MAGIC:
        raise ValueError(f"signature inattendue : {magic!r}")
    if version != COMMUNE_VERSION:
        raise ValueError(f"version de format non gérée : {version}")

    taille_fixe = struct.calcsize(_COMMUNE_FIXED)
    decalage = struct.calcsize(_COMMUNE_HEADER)
    communes = []
    for _ in range(n_communes):
        insee, cell_id, lat, lon, altitude, distance = struct.unpack_from(
            _COMMUNE_FIXED, blob, decalage
        )
        decalage += taille_fixe
        (longueur,) = struct.unpack_from("<B", blob, decalage)
        decalage += 1
        nom = blob[decalage : decalage + longueur].decode("utf-8")
        decalage += longueur

        (n_codes,) = struct.unpack_from("<B", blob, decalage)
        decalage += 1
        codes_postaux = []
        for _ in range(n_codes):
            codes_postaux.append(
                blob[decalage : decalage + POSTAL_CODE_BYTES].decode("ascii")
            )
            decalage += POSTAL_CODE_BYTES

        code = insee.decode("ascii")
        communes.append(
            Commune(
                insee=code,
                nom=nom,
                departement=code[:2],
                lat=lat / COORD_SCALE,
                lon=lon / COORD_SCALE,
                altitude=float(altitude),
                cell_id=cell_id,
                distance_km=distance / DISTANCE_SCALE,
                codes_postaux=codes_postaux,
            )
        )
    return communes
