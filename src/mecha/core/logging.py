"""Log redaction: every log record is scrubbed of credential-looking strings at creation.

Installed as a LogRecord factory so it covers every logger and handler (uvicorn, httpx,
third parties), including exception text, which is pre-rendered and redacted.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from mecha.core.redact import redact_text

_installed = False


def install_log_redaction() -> None:
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
        except Exception:  # malformed %-args: keep raw msg
            message = str(record.msg)
        record.msg = redact_text(message)
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            rendered = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact_text(rendered.rstrip("\n"))
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
