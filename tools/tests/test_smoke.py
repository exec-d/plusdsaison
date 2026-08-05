"""Vérifie que le package est importable et expose sa version."""

import plusdsaison


def test_le_package_expose_sa_version():
    assert isinstance(plusdsaison.__version__, str)
    assert plusdsaison.__version__
