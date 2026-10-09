"""Ephemeral per-query protection with nested JSON handling and no persisted keys."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from sqlctx.core.enums import SensitivityClass
from sqlctx.security.masking import DeterministicMaskingEngine
from sqlctx.security.protector import ValueProtector


class EphemeralQueryMasker:
    """One random key per query: fakes are consistent inside a result and unlinkable across."""

    def __init__(
        self,
        classifier: DeterministicMaskingEngine,
        column_classes: Mapping[str, SensitivityClass] | None = None,
    ) -> None:
        self.classifier = classifier
        self.protector = ValueProtector(secrets.token_bytes(32), column_classes=column_classes)

    def column_class(self, column_name: str) -> SensitivityClass:
        return self.protector.column_class(column_name)

    def mask(
        self, column_name: str, value: Any, sensitivity: SensitivityClass | None = None
    ) -> Any:
        return self.protector.protect(column_name, value, sensitivity)
