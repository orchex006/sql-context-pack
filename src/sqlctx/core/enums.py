"""Closed domain enumerations."""

from enum import StrEnum


class DatabaseEngine(StrEnum):
    SQLSERVER = "sqlserver"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    ORACLE = "oracle"
    POSTGRES = "postgres"


class ObjectType(StrEnum):
    TABLE = "table"
    PROCEDURE = "procedure"
    FUNCTION = "function"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_SELECTION = "awaiting_selection"
    AWAITING_OWNER = "awaiting_owner"
    READY = "ready"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EdgeType(StrEnum):
    FOREIGN_KEY = "foreign_key"
    ROUTINE_READ = "routine_read"
    ROUTINE_WRITE = "routine_write"
    ROUTINE_CALL = "routine_call"
    BOUNDARY = "boundary"


class SensitivityClass(StrEnum):
    PUBLIC = "public"
    NATIONAL_ID = "national_id"
    USERNAME = "username"
    PASSWORD = "password"
    PASSWORD_HASH = "password_hash"
    SECRET = "secret"
    SECRET_KEY = "secret_key"
    PRIVATE_KEY = "private_key"
    API_KEY = "api_key"
    CLIENT_SECRET = "client_secret"
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"
    JWT = "jwt"
    SESSION_TOKEN = "session_token"
    COOKIE = "cookie"
    EMAIL = "email"
    PHONE = "phone"
    ADDRESS = "address"
    PERSONAL_NAME = "personal_name"
    FINANCIAL_ACCOUNT = "financial_account"
    CREDIT_CARD = "credit_card"
    DATE_OF_BIRTH = "date_of_birth"
    PRECISE_LOCATION = "precise_location"
    BIOMETRIC = "biometric"
    UNKNOWN_SENSITIVE = "unknown_sensitive"


class ClassificationStatus(StrEnum):
    PRELIMINARY = "preliminary"
    FINAL_SUGGESTED = "final_suggested"
    FINAL_CONFIRMED = "final_confirmed"
    FINAL_UNRESOLVED = "final_unresolved"


class ClassificationPass(StrEnum):
    PASS_1 = "pass_1"
    PASS_2 = "pass_2"


class MaterializationMode(StrEnum):
    ASK = "ask"
    ALL = "all"
    SELECTED = "selected"


class InclusionReason(StrEnum):
    ALL_MODE = "all_mode"
    SELECTED_CATEGORY = "selected_category"
    POLICY_ALWAYS_INCLUDE = "policy_always_include"
    INTENTIONALLY_EXCLUDED = "intentionally_excluded"
    SECURITY_EXCLUDED = "security_excluded"
    BOUNDARY_INDEX_ONLY = "boundary_index_only"


class FormatStatus(StrEnum):
    FORMATTED = "formatted"
    PARSE_FAILED = "parse_failed"
    FORMAT_FAILED = "format_failed"
    ROLLED_BACK = "rolled_back"


class OutputProfile(StrEnum):
    AI = "ai"
    FULL = "full"


class SampleOutputFormat(StrEnum):
    MARKDOWN = "markdown"
    CSV = "csv"
    JSON = "json"


class ConstraintType(StrEnum):
    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    UNIQUE = "unique"
    CHECK = "check"
