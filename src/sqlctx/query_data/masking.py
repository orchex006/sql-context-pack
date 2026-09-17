"""Ephemeral per-query masking with nested JSON protection and no persisted keys."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from sqlctx.core.enums import SensitivityClass
from sqlctx.security.masking import HIGH_RISK, DeterministicMaskingEngine

_ALIASED = {
    SensitivityClass.USERNAME,
    SensitivityClass.NATIONAL_ID,
    SensitivityClass.EMAIL,
    SensitivityClass.PHONE,
    SensitivityClass.FINANCIAL_ACCOUNT,
    SensitivityClass.CREDIT_CARD,
}

_PREFIX = {
    SensitivityClass.USERNAME: "user",
    SensitivityClass.EMAIL: "user",
    SensitivityClass.PHONE: "phone",
    SensitivityClass.NATIONAL_ID: "id",
    SensitivityClass.FINANCIAL_ACCOUNT: "account",
    SensitivityClass.CREDIT_CARD: "card",
}


class EphemeralQueryMasker:
    def __init__(self, classifier: DeterministicMaskingEngine) -> None:
        self.classifier = classifier
        self.key = secrets.token_bytes(32)
        self.aliases: dict[tuple[SensitivityClass, str], str] = {}

    def mask(self, column_name: str, value: Any) -> Any:
        if self.classifier.classify(column_name, value) in HIGH_RISK:
            return None if value is None else "[REDACTED]"
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    masked = self._mask_json(parsed, column_name)
                    return json.dumps(masked, ensure_ascii=False, separators=(",", ":"))
        return self._mask_scalar(column_name, value)

    def _mask_json(self, value: Any, parent_name: str) -> Any:
        if self.classifier.classify(parent_name, value) in HIGH_RISK:
            return None if value is None else "[REDACTED]"
        if isinstance(value, dict):
            return {str(key): self._mask_json(item, str(key)) for key, item in value.items()}
        if isinstance(value, list):
            return [self._mask_json(item, parent_name) for item in value]
        return self._mask_scalar(parent_name, value)

    def _mask_scalar(self, column_name: str, value: Any) -> Any:
        sensitivity = self.classifier.classify(column_name, value)
        if value is None or sensitivity == SensitivityClass.PUBLIC:
            return value
        if sensitivity in HIGH_RISK:
            return "[REDACTED]"
        if sensitivity in _ALIASED:
            normalized = str(value).strip().casefold()
            key = (sensitivity, normalized)
            existing = self.aliases.get(key)
            if existing is not None:
                return existing
            digest = hmac.new(self.key, normalized.encode(), hashlib.sha256).hexdigest()[:12]
            alias = f"{_PREFIX[sensitivity]}_{digest}"
            if sensitivity == SensitivityClass.EMAIL:
                alias += "@example.invalid"
            self.aliases[key] = alias
            return alias
        return f"[{sensitivity.value.upper()}]"
