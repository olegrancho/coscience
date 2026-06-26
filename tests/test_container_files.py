from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_has_required_directives():
    text = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.12-slim" in text
    assert ".[http]" in text                      # installs the http extra
    assert "EXPOSE 8000" in text
    assert "coscience-http" in text               # runs the console script
    assert "COSCIENCE_REPO=/data" in text
    assert "AS ui" in text                        # node build stage
    assert "npm run build" in text                # builds the SPA
    assert "frontend/dist" in text                # copies the bundle in


def test_compose_service_shape():
    spec = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    svc = spec["services"]["coscience"]
    assert svc["build"] == "."
    assert "8000:8000" in svc["ports"]
    assert any(v.endswith(":/data") for v in svc["volumes"])
    assert svc["environment"]["COSCIENCE_REPO"] == "/data"
