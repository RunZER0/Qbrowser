from __future__ import annotations

import sys
import types

import pytest

from qbrowser import credentials


def test_environment_secret_takes_precedence_and_keyring_is_opt_in(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(get_password=lambda *args: calls.append(args) or "from-keyring")
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setenv("QBROWSER_API_TOKEN", "from-env")
    monkeypatch.setenv("QBROWSER_USE_KEYRING", "1")
    assert credentials.get_secret("QBROWSER_API_TOKEN") == "from-env"
    assert calls == []

    monkeypatch.delenv("QBROWSER_API_TOKEN")
    assert credentials.get_secret("QBROWSER_API_TOKEN") == "from-keyring"
    assert calls == [("qbrowser", "QBROWSER_API_TOKEN")]


def test_keyring_read_fails_closed_when_keyring_is_unavailable(monkeypatch):
    monkeypatch.delenv("QBROWSER_API_TOKEN", raising=False)
    monkeypatch.setenv("QBROWSER_USE_KEYRING", "1")
    monkeypatch.setitem(sys.modules, "keyring", None)
    assert credentials.get_secret("QBROWSER_API_TOKEN") == ""


def test_secret_names_are_allowlisted():
    with pytest.raises(ValueError):
        credentials.get_secret("HOME")
    with pytest.raises(ValueError):
        credentials.set_secret("HOME", "not a credential")


def test_set_and_delete_use_qbrowser_service(monkeypatch):
    saved = {}

    class PasswordDeleteError(Exception):
        pass

    errors = types.SimpleNamespace(PasswordDeleteError=PasswordDeleteError)
    fake = types.ModuleType("keyring")
    fake.set_password = lambda service, name, value: saved.update({(service, name): value})
    def remove(service, name):
        if saved.pop((service, name), None) is None:
            raise PasswordDeleteError
    fake.delete_password = remove
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setitem(sys.modules, "keyring.errors", errors)
    credentials.set_secret("QBROWSER_API_TOKEN", "secret value")
    assert saved[("qbrowser", "QBROWSER_API_TOKEN")] == "secret value"
    assert credentials.delete_secret("QBROWSER_API_TOKEN") is True
    assert credentials.delete_secret("QBROWSER_API_TOKEN") is False
