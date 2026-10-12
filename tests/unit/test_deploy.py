"""CD helpers (spec §12): the smoke test used after every deploy, and the image slimming list.

The smoke test runs here against a real uvicorn server on 127.0.0.1, so the exact script CD
uses on the live site is proven to pass on a good build and to fail on a bad one.
"""

import importlib.util
import shutil
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import uvicorn
from pydantic import SecretStr

from codeatlas import __version__
from codeatlas.api.app import create_app
from codeatlas.api.settings import WebSettings

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bundles"


def _load(name: str) -> ModuleType:
    # scripts/ is not a package, so load the file directly.
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load("smoke_test")
excludes = _load("docker_excludes")


def _serve(settings: WebSettings) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


@pytest.fixture
def live_url() -> Iterator[str]:
    yield from _serve(WebSettings(bundle_dir=FIXTURES, commit="abc1234"))


@pytest.fixture
def auth_url() -> Iterator[str]:
    yield from _serve(
        WebSettings(bundle_dir=FIXTURES, web_user="demo", web_password=SecretStr("demo-pass-123"))
    )


def test_smoke_passes_on_a_good_build(live_url: str) -> None:
    passed = smoke.run(smoke.Client(live_url), __version__, "abc1234", attempts=3, delay=0.1)
    assert any("replay" in line for line in passed)
    assert any("/health ok" in line for line in passed)


def test_smoke_main_exit_codes(live_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert smoke.main([live_url, "--version", __version__, "--attempts", "3"]) == 0
    assert "Smoke test passed" in capsys.readouterr().out
    assert smoke.main([live_url, "--version", "9.9.9", "--attempts", "3"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_smoke_fails_when_commit_differs(live_url: str) -> None:
    with pytest.raises(smoke.SmokeError, match="commit"):
        smoke.run(smoke.Client(live_url), expect_commit="someothersha", attempts=3, delay=0.1)


def test_smoke_with_basic_auth(auth_url: str) -> None:
    good = smoke.Client(auth_url, user="demo", password="demo-pass-123")
    passed = smoke.run(good, attempts=3, delay=0.1)
    assert any("401" in line for line in passed)
    bad = smoke.Client(auth_url, user="demo", password="wrong")
    with pytest.raises(smoke.SmokeError, match="/api/cases gave HTTP 401"):
        smoke.run(bad, attempts=3, delay=0.1)


def test_smoke_fails_on_tampered_bundle(tmp_path: Path) -> None:
    root = tmp_path / "bundles"
    shutil.copytree(FIXTURES, root)
    for bundle_dir in root.iterdir():
        bundle_file = bundle_dir / "bundle.json"
        bundle_file.write_bytes(bundle_file.read_bytes().replace(b'"', b"'", 1))
    for url in _serve(WebSettings(bundle_dir=root)):
        with pytest.raises(smoke.SmokeError, match="integrity"):
            smoke.run(smoke.Client(url), attempts=3, delay=0.1)


def test_smoke_reports_unreachable_server(capsys: pytest.CaptureFixture[str]) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens on this port now
    code = smoke.main([f"http://127.0.0.1:{port}", "--attempts", "2", "--delay", "0.05"])
    assert code == 1
    assert "never returned 200" in capsys.readouterr().out


def test_smoke_refuses_non_http_urls() -> None:
    with pytest.raises(smoke.SmokeError, match="non-HTTP"):
        smoke.Client("file:///etc").get("/passwd")


def test_image_leaves_out_only_the_embedding_stack() -> None:
    names = set(excludes.excluded_packages(ROOT / "uv.lock"))
    assert {"sentence-transformers", "torch"} <= names
    # Everything the web service imports must stay in the image.
    for needed in ("fastapi", "uvicorn", "jinja2", "pydantic", "numpy", "rank-bm25", "orjson"):
        assert needed not in names, needed


def test_excludes_cli_prints_uv_flags(capsys: pytest.CaptureFixture[str]) -> None:
    assert excludes.main(["docker_excludes.py", str(ROOT / "uv.lock")]) == 0
    out = capsys.readouterr().out
    assert "--no-install-package torch" in out
    assert "jinja2" not in out
