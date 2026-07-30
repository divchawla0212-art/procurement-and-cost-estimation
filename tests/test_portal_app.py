import os

import pytest

from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portal", "app.py")


def _run_app(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def test_create_opens_the_new_project(tmp_path, monkeypatch):
    """Creating a project must open it, not drop the user back on the <new> screen."""
    at = _run_app(tmp_path, monkeypatch)

    at.sidebar.text_input[0].set_value("Alpha Site")
    at.sidebar.button[0].click()
    at.run()

    assert not at.exception
    assert (tmp_path / "alpha-site" / "project.json").exists()
    assert at.sidebar.selectbox[0].value == "alpha-site"


def test_create_without_a_name_reports_an_error(tmp_path, monkeypatch):
    """A blank name must say so rather than silently doing nothing."""
    at = _run_app(tmp_path, monkeypatch)

    at.sidebar.button[0].click()
    at.run()

    assert not at.exception
    assert os.listdir(tmp_path) == []
    assert any("name" in err.value.lower() for err in at.sidebar.error)


def test_missing_api_key_does_not_block_creation(tmp_path, monkeypatch):
    """The ANTHROPIC_API_KEY warning is advisory; project creation must still work."""
    at = _run_app(tmp_path, monkeypatch)

    assert any("ANTHROPIC_API_KEY" in w.value for w in at.warning)

    at.sidebar.text_input[0].set_value("No Key Project")
    at.sidebar.button[0].click()
    at.run()

    assert not at.exception
    assert (tmp_path / "no-key-project" / "project.json").exists()
