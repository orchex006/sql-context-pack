"""Owner-side access grants confined to explicitly registered SQL roots."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from sqlctx.core.errors import SqlCtxError

SERVICE_ACCOUNT = "NT SERVICE\\SQLContextPack"


def grant_folder_access(input_root: Path, output_root: Path) -> None:
    if os.name != "nt":
        return
    # This is called only by owner registration/migration, never by an MCP plan/apply.
    roots = {input_root.resolve(), output_root.resolve()}
    protected = [
        Path(os.environ[name]).resolve()
        for name in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "PROGRAMDATA")
        if os.environ.get(name)
    ]
    for root in roots:
        if (
            root == Path(root.anchor)
            or str(root).startswith("\\\\")
            or any(
                root == item or root.is_relative_to(item) or item.is_relative_to(root)
                for item in protected
            )
        ):
            raise SqlCtxError(
                "SERVICE_FOLDER_SCOPE_UNSAFE",
                "Register a dedicated local SQL folder outside system/application directories; network shares require an explicitly configured service identity.",
            )
    executable = shutil.which("icacls")
    if executable is None:
        raise SqlCtxError("SERVICE_FOLDER_ACCESS_FAILED", "Windows ACL tooling is unavailable.")
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(  # noqa: S603 - fixed ACL tool and explicit owner roots.
            [executable, str(root), "/grant", f"{SERVICE_ACCOUNT}:(OI)(CI)M"],
            check=False,
            capture_output=True,
            text=True,
        )  # noqa: S603 - closed identity/rights and explicit owner-selected local roots.
        if result.returncode != 0:
            raise SqlCtxError(
                "SERVICE_FOLDER_ACCESS_FAILED",
                "The owner could not grant the SQLContextPack service access to a registered folder. Install the service first and check folder ownership.",
            )
