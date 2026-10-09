"""Column and value sensitivity rules, including Thai/AgriMap naming conventions.

Classification is by column name first, then by database classification, then by value
shape. Every non-public class is protected before a value can leave the service; see
`sqlctx.security.protector` for the treatment applied to each class.
"""

from __future__ import annotations

import re
from typing import Literal

from sqlctx.core.enums import SensitivityClass

Treatment = Literal["public", "fake", "alias", "generalize", "redact", "scan"]

HIGH_RISK = frozenset(
    {
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
        SensitivityClass.UNKNOWN_SENSITIVE,
    }
)

TREATMENT: dict[SensitivityClass, Treatment] = {
    SensitivityClass.PUBLIC: "public",
    SensitivityClass.FREE_TEXT: "scan",
    SensitivityClass.PERSONAL_NAME: "fake",
    SensitivityClass.NATIONAL_ID: "fake",
    SensitivityClass.PHONE: "fake",
    SensitivityClass.FINANCIAL_ACCOUNT: "fake",
    SensitivityClass.CREDIT_CARD: "fake",
    SensitivityClass.EMAIL: "alias",
    SensitivityClass.USERNAME: "alias",
    SensitivityClass.ADDRESS: "generalize",
    SensitivityClass.DATE_OF_BIRTH: "generalize",
    SensitivityClass.PRECISE_LOCATION: "generalize",
    **{item: "redact" for item in HIGH_RISK},
}

_PERSON_NOUNS = frozenset(
    {
        "farmer",
        "owner",
        "person",
        "contact",
        "customer",
        "cust",
        "cus",
        "member",
        "employee",
        "emp",
        "staff",
        "applicant",
        "citizen",
        "holder",
        "officer",
        "patient",
        "student",
        "teacher",
        "driver",
        "buyer",
        "seller",
        "landlord",
        "tenant",
        "spouse",
        "father",
        "mother",
        "parent",
        "beneficiary",
        "heir",
        "approver",
        "requester",
        "receiver",
        "recipient",
        "sender",
        "signer",
        "witness",
        "agent",
        "user",
    }
)
_NAME_QUALIFIERS = frozenset({"first", "last", "middle", "given", "family", "full", "nick", "sur"})
_NAME_COMPOUNDS = frozenset(
    {
        "fname",
        "lname",
        "mname",
        "firstname",
        "lastname",
        "middlename",
        "surname",
        "fullname",
        "nickname",
        "givenname",
        "familyname",
        "personname",
        "personalname",
    }
)
_ACTOR_VERBS = frozenset(
    {
        "create",
        "created",
        "update",
        "updated",
        "modify",
        "modified",
        "edit",
        "edited",
        "approve",
        "approved",
        "delete",
        "deleted",
        "insert",
        "inserted",
    }
)
_FREE_TEXT = frozenset(
    {
        "remark",
        "remarks",
        "note",
        "notes",
        "comment",
        "comments",
        "description",
        "desc",
        "detail",
        "details",
        "memo",
        "message",
        "msg",
        "reason",
        "feedback",
        "observation",
        "body",
        "content",
    }
)
_PASS_NEUTRAL = frozenset({"is", "status", "flag", "count", "date", "by", "rate", "score"})


def tokens(column_name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", column_name)
    normalized = re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")
    return [item for item in normalized.split("_") if item]


def classify_column_name(column_name: str) -> SensitivityClass:
    """Classify by name alone; PUBLIC means no name rule matched."""
    parts = tokens(column_name)
    if not parts:
        return SensitivityClass.PUBLIC
    token_set = set(parts)
    joined = "".join(parts)

    def has(*words: str) -> bool:
        return set(words).issubset(token_set)

    # High-risk credentials first: they must win over every softer rule.
    if token_set & {"jwt"}:
        return SensitivityClass.JWT
    if token_set & {"cookie", "cookies"}:
        return SensitivityClass.COOKIE
    if token_set & {"biometric", "fingerprint"} or has("face", "template"):
        return SensitivityClass.BIOMETRIC
    if has("private", "key") or "privatekey" in token_set:
        return SensitivityClass.PRIVATE_KEY
    if has("api", "key") or "apikey" in token_set:
        return SensitivityClass.API_KEY
    if has("client", "secret"):
        return SensitivityClass.CLIENT_SECRET
    if has("secret", "key"):
        return SensitivityClass.SECRET_KEY
    if token_set & {"token", "tokens"}:
        if "refresh" in token_set:
            return SensitivityClass.REFRESH_TOKEN
        if "session" in token_set:
            return SensitivityClass.SESSION_TOKEN
        return SensitivityClass.ACCESS_TOKEN
    password_words = {"password", "passwd", "pwd", "passcode", "passphrase"}
    if token_set & password_words or ("pass" in token_set and not token_set & _PASS_NEUTRAL):
        if token_set & {"hash", "hashed", "digest"}:
            return SensitivityClass.PASSWORD_HASH
        return SensitivityClass.PASSWORD
    if token_set & {"secret", "salt", "otp", "cvv", "cvc"} or has("pin", "code"):
        return SensitivityClass.SECRET

    if (
        token_set & {"citizen", "citizenid", "idcard", "pid", "passport", "ssn", "taxid"}
        or has("id", "card")
        or has("national", "id")
        or has("personal", "id")
        or has("tax", "id")
        or joined in {"nationalid", "personalid"}
    ):
        return SensitivityClass.NATIONAL_ID
    if (
        has("credit", "card")
        or token_set & {"cardno"}
        or ("card" in token_set and token_set & {"no", "number", "num"})
    ):
        return SensitivityClass.CREDIT_CARD
    if (
        token_set & {"iban", "promptpay", "accno", "accountno"}
        or ("bank" in token_set and token_set & {"acc", "account", "no", "number"})
        or (token_set & {"acc", "account"} and token_set & {"no", "number", "num"})
    ):
        return SensitivityClass.FINANCIAL_ACCOUNT
    if token_set & {"email", "mail"} or has("e", "mail"):
        return SensitivityClass.EMAIL
    if token_set & {"phone", "tel", "telephone", "mobile", "fax", "msisdn", "cellphone"}:
        return SensitivityClass.PHONE
    if (
        token_set & {"username", "login", "loginid"}
        or joined == "username"
        or ("by" in token_set and token_set & _ACTOR_VERBS)
    ):
        return SensitivityClass.USERNAME
    if (
        token_set & _NAME_COMPOUNDS
        or ("name" in token_set and token_set & (_NAME_QUALIFIERS | _PERSON_NOUNS))
        or joined in _NAME_COMPOUNDS
    ):
        return SensitivityClass.PERSONAL_NAME
    if (
        token_set & {"address", "addr", "houseno", "street", "soi"}
        or has("house", "no")
        or has("house", "number")
    ):
        return SensitivityClass.ADDRESS
    if token_set & {"dob", "birth", "birthday", "birthdate", "born"}:
        return SensitivityClass.DATE_OF_BIRTH
    if token_set & {
        "lat",
        "latitude",
        "lon",
        "lng",
        "longitude",
        "geom",
        "geometry",
        "geog",
        "geography",
        "gps",
        "coord",
        "coords",
        "coordinate",
        "coordinates",
        "wkt",
        "utm",
    }:
        return SensitivityClass.PRECISE_LOCATION
    if token_set & _FREE_TEXT:
        return SensitivityClass.FREE_TEXT
    return SensitivityClass.PUBLIC


THAI_ID = re.compile(r"(?<![0-9])[0-9](?:[ -]?[0-9]){12}(?![0-9])")
_WHOLE_THAI_ID = re.compile(r"^[0-9](?:[ -]?[0-9]){12}$")
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
# Bounded repetitions keep scanning linear on large text values (RFC 5321 length limits).
EMAIL = re.compile(r"[^@\s<>()\[\],;:\"']{1,64}@[^@\s<>()\[\],;:\"']{1,253}\.[A-Za-z]{2,24}")
_WHOLE_EMAIL = re.compile(r"^[^@\s\"'{}\[\]<>,;:]+@[^@\s\"'{}\[\]<>,;:]+\.[A-Za-z]{2,}$")
PHONE = re.compile(
    r"(?<![0-9])(?:\+66[ -]?|0)[0-9]{1,2}[ -]?[0-9]{3}[ -]?[0-9]{3,4}(?![0-9])"
    r"|(?<![0-9])\+[1-9][0-9 ()-]{7,20}[0-9]"
)
_WHOLE_PHONE = re.compile(
    r"^(?:(?:\+66[ -]?|0)[0-9]{1,2}[ -]?[0-9]{3}[ -]?[0-9]{3,4}|\+[1-9][0-9 ()-]{7,20}[0-9])$"
)
CARD = re.compile(r"(?<![0-9])[0-9]{4}(?:[ -]?[0-9]{4}){3}(?:[ -]?[0-9]{1,3})?(?![0-9])")
_WHOLE_CARD = re.compile(r"^(?:[0-9][ -]*?){14,19}$")
THAI_TITLED_NAME = re.compile(
    r"(?P<title>นางสาว|นาง|นาย|น\.ส\.|ด\.ช\.|ด\.ญ\.|เด็กชาย|เด็กหญิง|คุณ)\s*"
    r"(?P<first>[ก-๏]{2,})(?:\s+(?P<last>[ก-๏]{2,}))?"
)
LATIN_TITLED_NAME = re.compile(
    r"\b(?P<title>Mr|Mrs|Ms|Miss|Dr)\.?\s+(?P<first>[A-Z][a-z]+)(?:\s+(?P<last>[A-Z][a-z]+))?"
)


def classify_value(value: object) -> SensitivityClass:
    """Classify a whole scalar value by shape; PUBLIC means no pattern matched."""
    if not isinstance(value, str):
        return SensitivityClass.PUBLIC
    text = value.strip()
    if not text:
        return SensitivityClass.PUBLIC
    if _JWT.fullmatch(text) and not re.fullmatch(r"[0-9.]+", text):
        return SensitivityClass.JWT
    if _WHOLE_EMAIL.fullmatch(text):
        return SensitivityClass.EMAIL
    if _WHOLE_THAI_ID.fullmatch(text):
        return SensitivityClass.NATIONAL_ID
    if _WHOLE_PHONE.fullmatch(text):
        return SensitivityClass.PHONE
    if _WHOLE_CARD.fullmatch(text):
        return SensitivityClass.CREDIT_CARD
    titled = THAI_TITLED_NAME.fullmatch(text) or LATIN_TITLED_NAME.fullmatch(text)
    if titled:
        return SensitivityClass.PERSONAL_NAME
    return SensitivityClass.PUBLIC


def classify_database_label(information_type: str | None, label: str | None) -> SensitivityClass:
    """Map a SQL Server sensitivity classification to a sqlctx class.

    Any classified column is sensitive; only an explicit public label without an
    information type is left public.
    """
    info = (information_type or "").strip().casefold()
    tag = (label or "").strip().casefold()
    if not info and (not tag or tag == "public"):
        return SensitivityClass.PUBLIC
    if "credential" in info or "password" in info:
        return SensitivityClass.PASSWORD
    if "name" in info:
        return SensitivityClass.PERSONAL_NAME
    if "national" in info or "ssn" in info or info in {"id", "identification"}:
        return SensitivityClass.NATIONAL_ID
    if "credit" in info:
        return SensitivityClass.CREDIT_CARD
    if "financial" in info or "bank" in info:
        return SensitivityClass.FINANCIAL_ACCOUNT
    if "birth" in info:
        return SensitivityClass.DATE_OF_BIRTH
    if "email" in info:
        return SensitivityClass.EMAIL
    if "phone" in info:
        return SensitivityClass.PHONE
    if "address" in info:
        return SensitivityClass.ADDRESS
    if "location" in info:
        return SensitivityClass.PRECISE_LOCATION
    return SensitivityClass.UNKNOWN_SENSITIVE


def thai_id_check_digit(first_twelve: str) -> int:
    total = sum(int(digit) * (13 - index) for index, digit in enumerate(first_twelve))
    return (11 - total % 11) % 10
