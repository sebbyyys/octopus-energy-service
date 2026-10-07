"""Tests must never inherit a user's upstream credentials or local configuration."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolated_octopus_environment(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("OCTOPUS_"):
            monkeypatch.delenv(name)
