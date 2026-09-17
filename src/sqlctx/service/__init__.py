"""Cross-platform supervision for the single shared loopback service."""

from sqlctx.service.manager import HostOS, Operation, detect_host_os, manage

__all__ = ["HostOS", "Operation", "detect_host_os", "manage"]
