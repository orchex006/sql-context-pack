from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_install_guide() -> object:
    path = ROOT / "scripts/install-guide.py"
    spec = importlib.util.spec_from_file_location("install_guide", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_detect_host_os_classifies_supported_platforms() -> None:
    guide = load_install_guide()

    assert guide.detect_host_os("Windows") == "windows"  # type: ignore[attr-defined]
    assert guide.detect_host_os("Darwin") == "macos"  # type: ignore[attr-defined]
    assert guide.detect_host_os("Linux") == "linux"  # type: ignore[attr-defined]
    assert guide.detect_host_os("FreeBSD") == "unix"  # type: ignore[attr-defined]


def test_windows_guide_routes_to_managed_setup() -> None:
    guide = load_install_guide()

    text = guide.install_guide("windows")  # type: ignore[attr-defined]

    assert "Managed Agent runtime: supported" in text
    assert "$sql-context-pack setup" in text
    assert "/sql-context-pack:sql-context-pack setup" in text
    assert "/skills list" in text
    assert "Use the sql-context-pack skill to run setup." in text
    assert "SQLContextPack Windows Service" in text
    assert "sqlctx-mcp-bridge" in text


def test_non_windows_guides_route_to_cross_platform_managed_runtime() -> None:
    guide = load_install_guide()

    for host_os in ("macos", "linux", "unix"):
        text = guide.install_guide(host_os)  # type: ignore[attr-defined]
        assert "Managed Agent runtime: supported" in text
        assert "$sql-context-pack setup" in text
        assert "/sql-context-pack:sql-context-pack setup" in text
        assert "/skills list" in text
        assert "Use the sql-context-pack skill to run setup." in text
        assert "authenticated loopback health" in text
