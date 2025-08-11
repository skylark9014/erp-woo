# --- Global log sanitizer to stop HTML body spam --------------------------------
import logging, re

_HTML_SIG_RE = re.compile(r'(?is)<!DOCTYPE html|<html[^>]*>')
_TITLE_RE    = re.compile(r'(?is)<title[^>]*>(.*?)</title>')
_TAG_RE      = re.compile(r'(?is)<[^>]+>')
_SCRIPT_RE   = re.compile(r'(?is)<(script|style)[^>]*>.*?</\1>')

def _strip_tags(s: str) -> str:
    s = _SCRIPT_RE.sub('', s)
    s = _TAG_RE.sub(' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def _summarize_html(s: str, limit: int = 200) -> str:
    title = None
    m = _TITLE_RE.search(s)
    if m:
        title = _strip_tags(m.group(1))
    preview = title or _strip_tags(s)[:limit]
    return f"{preview} [HTML {len(s)} chars trimmed]"

class _HtmlTrimFilter(logging.Filter):
    """If a log message contains a large HTML blob, replace it with a short summary."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if isinstance(msg, str) and len(msg) > 200 and _HTML_SIG_RE.search(msg):
                record.msg = _summarize_html(msg)
                record.args = ()
        except Exception:
            pass
        return True

# install once on common loggers (root + uvicorn family)
for _name in ("", "uvicorn", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_HtmlTrimFilter())
# --------------------------------------------------------------------------------
