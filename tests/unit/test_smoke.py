from fastapi.testclient import TestClient
from typer.testing import CliRunner

import codeatlas
from codeatlas.api.app import app
from codeatlas.cli import app as cli_app


def test_package_imports() -> None:
    assert codeatlas.__version__ == "0.1.0"


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cli_version() -> None:
    result = CliRunner().invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout
