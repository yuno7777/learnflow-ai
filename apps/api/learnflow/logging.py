"""Structured JSON logging and automated secret redaction module.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file configures the application-wide structured logging pipeline using `structlog`.
It provides:
1. Automated secret redaction (`_redact`) ensuring sensitive tokens, passwords,
   and API keys never leak into log files or stdout.
2. Context variable merging (`merge_contextvars`) so that the current `request_id`
   and HTTP path are automatically attached to every log emitted during a request.
3. Production JSON formatting vs Development pretty-console rendering.
4. `get_logger(name)` factory function used across all backend modules.

===============================================================================
WHY THIS FILE EXISTS & WHAT HAPPENS IF REMOVED:
===============================================================================
If removed:
- Log messages would revert to unstructured standard library text strings,
  making log aggregation (in Datadog, CloudWatch, or Grafana Loki) impossible.
- Secrets, JWTs, and HMAC signatures passed into log functions would be printed
  in plaintext, violating security compliance.
- Logs wouldn't have correlation IDs to trace an entire request lifecycle.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `learnflow/main.py`: Calls `configure_logging()` at app startup.
- `learnflow/middleware.py`: Uses `structlog.contextvars.bind_contextvars(request_id=...)`
  to attach the unique request ID to the logging context.
- Used everywhere via `from .logging import get_logger; log = get_logger(__name__)`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

# Substrings in dictionary keys that trigger automated value redaction
_SECRET_HINTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "signature",
    "key_hash",
)


def _redact(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Structlog Processor: scrubs sensitive keys before serialization.

    Iterates through all key-value pairs in the log event. If any key matches
    known secret patterns (e.g. `user_token`, `password_hash`, `api_key`),
    its value is replaced with `"[redacted]"`.
    """
    for key in list(event_dict):
        if any(hint in key.lower() for hint in _SECRET_HINTS):
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(json_output: bool = True, level: str = "INFO") -> None:
    """Configures the global Structlog processing pipeline.

    Processor Pipeline Execution Order:
    1. `merge_contextvars`: Pulls in `request_id` bound by ASGI middleware.
    2. `add_log_level`: Appends `level: "info" | "warning" | "error"`.
    3. `TimeStamper`: Appends ISO-8601 timestamp (`timestamp: "2026-08-20T..."`).
    4. `_redact`: Strips sensitive secrets from the dictionary.
    5. `format_exc_info`: Formats Python exception tracebacks cleanly.
    6. `JSONRenderer` / `ConsoleRenderer`: Serializes to single-line JSON (for production)
       or colorful human-readable text (for local development).
    """
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Creates a bound logger instance named after the calling module."""
    return structlog.get_logger(name)
