import pytest
from utils import has_access

def test_has_access_owner():
    assert has_access("owner", ["менеджер"]) is True
    assert has_access("owner", []) is True
    assert has_access("owner", ["официант", "бартендер"]) is True

def test_has_access_single_role():
    assert has_access("менеджер", ["менеджер"]) is True
    assert has_access("официант", ["официант"]) is True
    assert has_access("бартендер", ["официант"]) is False
    assert has_access("менеджер", ["официант", "бартендер"]) is False

def test_has_access_with_roles_list():
    # Когда передан список roles
    assert has_access("менеджер", ["официант"], roles=["менеджер"]) is True
    assert has_access("официант", ["менеджер"], roles=["бартендер", "менеджер"]) is True
    assert has_access("официант", ["менеджер"], roles=["бартендер"]) is False
    assert has_access("официант", ["официант", "бартендер"], roles=["бартендер"]) is True

def test_has_access_empty_roles():
    assert has_access("гость", ["менеджер"], roles=[]) is False
    assert has_access(None, ["менеджер"], roles=None) is False