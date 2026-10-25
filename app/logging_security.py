"""Keep credential query parameters out of server and HTTP-client access logs."""

import logging
import re
from urllib.parse import unquote_plus

_FIELDS = re.compile(r"""([?&])([^=&\s"']+)=([^&\s"']*)""")
_SECRET_NAMES = frozenset(
    {"token", "access_token", "refresh_token", "api_key", "key", "password", "code"}
)


class SensitiveQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()

        def replace(match):
            if unquote_plus(match[2]).lower() in _SECRET_NAMES:
                return f"{match[1]}{match[2]}=[redacted]"
            return match[0]

        redacted = _FIELDS.sub(replace, message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


def install_sensitive_query_filters() -> None:
    for name in ("uvicorn.error", "uvicorn.access", "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, SensitiveQueryFilter) for item in logger.filters):
            logger.addFilter(SensitiveQueryFilter())
