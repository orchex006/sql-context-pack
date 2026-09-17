"""Fail-closed sensitive-data classification, deterministic aliases, and SQL literal scanning."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import Any

from sqlctx.core.enums import SensitivityClass
from sqlctx.core.models import MaskingDecision
from sqlctx.security.runtime import EncryptedSnapshotSecretStore

CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"

HIGH_RISK = {
    SensitivityClass.PASSWORD,
    SensitivityClass.PASSWORD_HASH,
    SensitivityClass.SECRET,
    SensitivityClass.SECRET_KEY,
    SensitivityClass.PRIVATE_KEY,
    SensitivityClass.API_KEY,
    SensitivityClass.CLIENT_SECRET,
    SensitivityClass.ACCESS_TOKEN,
    SensitivityClass.REFRESH_TOKEN,
    SensitivityClass.JWT,
    SensitivityClass.SESSION_TOKEN,
    SensitivityClass.COOKIE,
    SensitivityClass.BIOMETRIC,
}

EXACT_RULES = {
    "national_id": SensitivityClass.NATIONAL_ID,
    "citizen_id": SensitivityClass.NATIONAL_ID,
    "username": SensitivityClass.USERNAME,
    "user_name": SensitivityClass.USERNAME,
    "password": SensitivityClass.PASSWORD,
    "password_hash": SensitivityClass.PASSWORD_HASH,
    "secret": SensitivityClass.SECRET,
    "secret_key": SensitivityClass.SECRET_KEY,
    "private_key": SensitivityClass.PRIVATE_KEY,
    "api_key": SensitivityClass.API_KEY,
    "client_secret": SensitivityClass.CLIENT_SECRET,
    "access_token": SensitivityClass.ACCESS_TOKEN,
    "refresh_token": SensitivityClass.REFRESH_TOKEN,
    "jwt": SensitivityClass.JWT,
    "session_token": SensitivityClass.SESSION_TOKEN,
    "cookie": SensitivityClass.COOKIE,
    "email": SensitivityClass.EMAIL,
    "phone": SensitivityClass.PHONE,
    "address": SensitivityClass.ADDRESS,
    "personal_name": SensitivityClass.PERSONAL_NAME,
    "full_name": SensitivityClass.PERSONAL_NAME,
    "financial_account": SensitivityClass.FINANCIAL_ACCOUNT,
    "credit_card": SensitivityClass.CREDIT_CARD,
    "date_of_birth": SensitivityClass.DATE_OF_BIRTH,
    "precise_location": SensitivityClass.PRECISE_LOCATION,
    "biometric": SensitivityClass.BIOMETRIC,
}

VALUE_PATTERNS = (
    (re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"), SensitivityClass.JWT),
    (re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"), SensitivityClass.EMAIL),
    (re.compile(r"^\+?[0-9][0-9() .-]{7,}$"), SensitivityClass.PHONE),
    (re.compile(r"^(?:[0-9][ -]*?){13,19}$"), SensitivityClass.CREDIT_CARD),
)


def _crockford(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    chars: list[str] = []
    while value:
        value, remainder = divmod(value, 32)
        chars.append(CROCKFORD[remainder])
    return ("".join(reversed(chars)) or "0").rjust(52, "0")


class DeterministicMaskingEngine:
    def __init__(self, secrets_store: EncryptedSnapshotSecretStore) -> None:
        self.secrets_store = secrets_store

    def classify(
        self,
        column_name: str,
        value: Any,
        *,
        owner_override: SensitivityClass | None = None,
        database_classification: SensitivityClass | None = None,
    ) -> SensitivityClass:
        if owner_override is not None:
            return owner_override
        if database_classification is not None:
            return database_classification
        normalized = re.sub(r"[^a-z0-9]+", "_", column_name.lower()).strip("_")
        if normalized in EXACT_RULES:
            return EXACT_RULES[normalized]
        tokens = set(normalized.split("_"))
        for key, sensitivity in EXACT_RULES.items():
            if set(key.split("_")).issubset(tokens):
                return sensitivity
        if isinstance(value, str):
            for pattern, sensitivity in VALUE_PATTERNS:
                if pattern.fullmatch(value.strip()):
                    return sensitivity
        return SensitivityClass.PUBLIC

    def mask(
        self,
        *,
        column_name: str,
        value: Any,
        snapshot_id: str,
        owner_override: SensitivityClass | None = None,
        database_classification: SensitivityClass | None = None,
    ) -> MaskingDecision:
        sensitivity = self.classify(
            column_name,
            value,
            owner_override=owner_override,
            database_classification=database_classification,
        )
        if value is None or sensitivity == SensitivityClass.PUBLIC:
            return MaskingDecision(
                sensitivity=sensitivity, action="keep", masked_value=value, rule="public"
            )
        if sensitivity in HIGH_RISK:
            return MaskingDecision(
                sensitivity=sensitivity,
                action="redact",
                masked_value="[REDACTED]",
                rule="high-risk-secret",
            )
        if sensitivity in {
            SensitivityClass.USERNAME,
            SensitivityClass.NATIONAL_ID,
            SensitivityClass.EMAIL,
            SensitivityClass.PHONE,
            SensitivityClass.FINANCIAL_ACCOUNT,
            SensitivityClass.CREDIT_CARD,
        }:
            alias = self._alias(snapshot_id, sensitivity, str(value))
            return MaskingDecision(
                sensitivity=sensitivity,
                action="alias",
                masked_value=alias,
                rule="snapshot-hmac",
            )
        return MaskingDecision(
            sensitivity=sensitivity,
            action="generalize",
            masked_value=f"[{sensitivity.value.upper()}]",
            rule="strict-generalization",
        )

    def _alias(self, snapshot_id: str, sensitivity: SensitivityClass, raw_value: str) -> str:
        key = self.secrets_store.get_or_create_key(snapshot_id)
        normalized = raw_value.strip().casefold().encode()
        digest = hmac.new(key, normalized, hashlib.sha256).digest()
        digest_hex = digest.hex()
        encoded = _crockford(digest)
        registry = self.secrets_store.load_registry(snapshot_id)
        reverse = {alias: known_digest for known_digest, alias in registry.items()}
        prefix = {
            SensitivityClass.USERNAME: "user",
            SensitivityClass.EMAIL: "user",
            SensitivityClass.PHONE: "phone",
            SensitivityClass.NATIONAL_ID: "id",
            SensitivityClass.FINANCIAL_ACCOUNT: "account",
            SensitivityClass.CREDIT_CARD: "card",
        }[sensitivity]
        length = 10
        while True:
            token = encoded[:length]
            alias = f"{prefix}_{token}"
            if sensitivity == SensitivityClass.EMAIL:
                alias += "@example.invalid"
            if alias not in reverse or reverse[alias] == digest_hex:
                break
            length += 2
        registry[digest_hex] = alias
        self.secrets_store.save_registry(snapshot_id, registry)
        return alias


_SQL_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(password|passwd|pwd|secret|api[_-]?key|token)\s*=\s*(['\"])(?!\[REDACTED\]\2)(.*?)\2"
    ),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]+"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.S,
    ),
)


def scan_and_redact_sql_literals(sql: str) -> tuple[str, int]:
    cleaned = sql
    count = 0
    for pattern in _SQL_SECRET_PATTERNS:
        if pattern.groups >= 3:
            cleaned, replaced = pattern.subn(
                lambda match: f"{match.group(1)}='[REDACTED]'", cleaned
            )
        else:
            cleaned, replaced = pattern.subn("[REDACTED]", cleaned)
        count += replaced
    return cleaned, count
