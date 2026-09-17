"""Migrate registered folder ACLs under the owner token before service elevation."""

from pathlib import Path

from sqlctx.security.runtime import JsonRuntimeStateStore
from sqlctx.security.service_access import grant_folder_access


def main() -> None:
    records = JsonRuntimeStateStore().read_json("managed-folders/registry.json", {})
    for record in records.values():
        grant_folder_access(Path(record["input_root"]), Path(record["output_root"]))
    print(f"Registered SQL folder access checked: {len(records)} registrations.")


if __name__ == "__main__":
    main()
