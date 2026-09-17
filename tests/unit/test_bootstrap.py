from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_bootstrap() -> object:
    path = ROOT / "scripts/bootstrap.py"
    spec = importlib.util.spec_from_file_location("bootstrap", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_windows_remove_is_rejected_before_installer_runs(monkeypatch: Any, tmp_path: Path) -> None:
    bootstrap = load_bootstrap()
    monkeypatch.setattr(bootstrap, "_host_os", lambda: "windows")
    monkeypatch.setattr(
        bootstrap,
        "_windows_bootstrap",
        lambda *_args, **_kwargs: pytest.fail("Windows installer must not run for remove."),
    )

    with pytest.raises(SystemExit, match="Windows removal is not available"):
        bootstrap.main(  # type: ignore[attr-defined]
            ["--operation", "remove", "--source-root", str(tmp_path)]
        )


@pytest.mark.parametrize("host_os", ["linux", "macos", "unix"])
def test_non_windows_remove_is_preserved(monkeypatch: Any, tmp_path: Path, host_os: str) -> None:
    bootstrap = load_bootstrap()
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(bootstrap, "_host_os", lambda: host_os)
    monkeypatch.setattr(
        bootstrap,
        "_posix_bootstrap",
        lambda root, operation: calls.append((root, operation)),
    )

    result = bootstrap.main(  # type: ignore[attr-defined]
        ["--operation", "remove", "--source-root", str(tmp_path)]
    )

    assert result == 0
    assert calls == [(tmp_path.resolve(), "remove")]


@pytest.mark.parametrize(
    ("arguments", "repair"),
    [([], False), (["--operation", "update"], True), (["--repair"], True)],
)
def test_windows_setup_and_update_routes_are_unchanged(
    monkeypatch: Any, tmp_path: Path, arguments: list[str], repair: bool
) -> None:
    bootstrap = load_bootstrap()
    calls: list[tuple[Path, bool]] = []
    monkeypatch.setattr(bootstrap, "_host_os", lambda: "windows")
    monkeypatch.setattr(
        bootstrap,
        "_windows_bootstrap",
        lambda root, repair: calls.append((root, repair)),
    )

    result = bootstrap.main(  # type: ignore[attr-defined]
        [*arguments, "--source-root", str(tmp_path)]
    )

    assert result == 0
    assert calls == [(tmp_path.resolve(), repair)]
