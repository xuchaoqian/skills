"""HTML → Markdown conversion for Claude web widgets/artifacts.

Public entry point: ``html_to_markdown(html_str) -> str``.

Dispatch strategy:
  * If the optional ``html-to-markdown`` library (PyPI, Rust core, CommonMark)
    is installed, use it for high-fidelity conversion.
  * Otherwise fall back to a dependency-free stdlib parser (``html.parser``)
    that handles the clean, machine-generated HTML Claude widgets emit:
    tables (the priority path), headings, lists, and light inline formatting.

This module is intentionally NOT named ``html_to_markdown.py`` so it does not
shadow the library package of that name.
"""

from __future__ import annotations

import os
import sys
from html.parser import HTMLParser

# Optional high-fidelity backend. Prefer a vendored copy bundled under
# scripts/_vendor/ (so the skill is self-contained, no system install needed),
# then fall back to any system-installed html_to_markdown, then to the stdlib
# parser below.
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

try:
    from html_to_markdown import convert as _lib_convert, ConversionOptions as _LibOptions
    _HAVE_LIB = True
    try:
        from importlib.metadata import version as _pkg_version
        _LIB_VERSION = _pkg_version("html-to-markdown")
    except Exception:
        _LIB_VERSION = ""
except Exception:
    _HAVE_LIB = False
    _LIB_VERSION = ""


def backend_name() -> str:
    """Human-readable name of the active HTML→Markdown backend."""
    if _HAVE_LIB:
        return f"html_to_markdown library{(' v' + _LIB_VERSION) if _LIB_VERSION else ''}"
    return "built-in stdlib parser"


# Defensive caps so a pathological widget can't produce runaway output.
MAX_TABLE_COLS = 1000
MAX_CELL_WIDTH = 2000

_SKIP_TAGS = {"script", "style", "svg", "canvas", "head", "title"}
_BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "tr"}
_HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}


def _escape_cell_data(text: str) -> str:
    """Escape markdown specials in literal text destined for a table cell.

    Escapes ``|`` (would split the cell), ``*`` and backtick (would open
    emphasis/code). Underscores are left alone: GFM/CommonMark — which Cursor
    uses — does not treat intra- or inter-word ``_`` as emphasis in the common
    cases here, and escaping them noisily uglifies identifiers like
    ``claude_haiku_4_5``.
    """
    out = []
    for ch in text:
        if ch in ("|", "*", "`"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


class _MarkdownExtractor(HTMLParser):
    """Best-effort HTML→Markdown. Tables are the priority path; other tags
    degrade gracefully to text with light inline formatting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []          # finished block-level Markdown chunks
        self._buf: list[str] = []         # current text run
        self._skip_depth = 0              # inside script/style/svg/canvas
        self._list_stack: list[str] = []  # 'ul' | 'ol'
        self._ol_index: list[int] = []
        # Table state. Each committed row is (is_header, cells); the header flag
        # travels with the row so filtering empty rows can't misalign it.
        self._in_table = 0
        self._rows: list[tuple[bool, list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._row_has_th = False
        self._href_stack: list[str] = []

    # -- text accumulation -------------------------------------------------
    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        else:
            self._buf.append(text)

    def _flush_buf(self) -> None:
        text = "".join(self._buf).strip()
        self._buf = []
        if text:
            self.out.append(text)

    def _commit_cell(self) -> None:
        """Commit the open cell to the current row (implicit or explicit close)."""
        if self._cell is not None:
            cell_text = self._clean_cell("".join(self._cell))
            if self._row is not None:
                self._row.append(cell_text)
            self._cell = None

    def _commit_row(self) -> None:
        """Commit the open row to the table (implicit or explicit close)."""
        self._commit_cell()
        if self._row is not None:
            self._rows.append((self._row_has_th, self._row))
            self._row = None
            self._row_has_th = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        # Collapse internal whitespace runs but keep single spaces.
        chunk = " ".join(data.split())
        if not chunk:
            return
        # Preserve a leading/trailing space if the original had one (inline flow).
        if data[:1].isspace():
            chunk = " " + chunk
        if data[-1:].isspace():
            chunk = chunk + " "
        # Inside a table cell, escape literal markdown specials that come from
        # TEXT (not markup). Markup markers like ** and ` are emitted directly
        # in handle_start/endtag and never pass through here, so escaping here
        # cannot corrupt legitimate <strong>/<em>/<code> conversions.
        if self._cell is not None:
            chunk = _escape_cell_data(chunk)
        self._emit(chunk)

    # -- tags --------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return

        if tag == "br":
            self._emit("<br>" if self._cell is not None else "\n")
            return
        if tag in ("strong", "b"):
            self._emit("**"); return
        if tag in ("em", "i"):
            self._emit("*"); return
        if tag == "code":
            self._emit("`"); return
        if tag == "a":
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = v
            self._href_stack.append(href)
            self._emit("["); return

        if tag == "table":
            if self._cell is None:
                self._flush_buf()
            self._in_table += 1
            if self._in_table == 1:
                self._rows = []
                self._row_has_th = False
            return
        # Only the outermost table is treated structurally; nested-table tr/td/th
        # markers fall through so their text flows into the current cell as text.
        if tag == "tr" and self._in_table == 1:
            # Implicitly close a still-open previous row (missing </tr>).
            self._commit_row()
            self._row = []
            self._row_has_th = False
            return
        if tag in ("td", "th") and self._in_table == 1:
            # Implicitly close a still-open previous cell (missing </td>/</th>).
            self._commit_cell()
            # A cell with no enclosing <tr> still needs a row to land in.
            if self._row is None:
                self._row = []
                self._row_has_th = False
            self._cell = []
            if tag == "th":
                self._row_has_th = True
            return

        if tag in _HEADING_TAGS and self._cell is None:
            self._flush_buf()
            self._buf.append(_HEADING_TAGS[tag] + " ")
            return
        if tag == "ul":
            self._flush_buf(); self._list_stack.append("ul"); return
        if tag == "ol":
            self._flush_buf(); self._list_stack.append("ol"); self._ol_index.append(0); return
        if tag == "li":
            self._flush_buf()
            if self._list_stack and self._list_stack[-1] == "ol":
                self._ol_index[-1] += 1
                self._buf.append(f"{self._ol_index[-1]}. ")
            else:
                self._buf.append("- ")
            return
        if tag in _BLOCK_TAGS:
            if self._cell is None:
                self._flush_buf()
            elif self._cell:
                # Block boundary inside a table cell → stacked line (becomes <br>).
                self._emit("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth -= 1
            return

        if tag in ("strong", "b"):
            self._emit("**"); return
        if tag in ("em", "i"):
            self._emit("*"); return
        if tag == "code":
            self._emit("`"); return
        if tag == "a":
            href = self._href_stack.pop() if self._href_stack else ""
            self._emit(f"]({href})" if href else "]")
            return

        if tag in ("td", "th") and self._in_table == 1 and self._cell is not None:
            self._commit_cell()
            return
        if tag == "tr" and self._in_table == 1 and self._row is not None:
            # Commits any still-open cell, then the row (missing </td>).
            self._commit_row()
            return
        if tag == "table" and self._in_table:
            self._in_table -= 1
            if self._in_table == 0:
                # Flush any cell/row left open by missing </td>/</tr>.
                self._commit_row()
                self._render_table()
            return

        if tag in _HEADING_TAGS and self._cell is None:
            self._flush_buf(); return
        if tag == "ul" and self._list_stack:
            self._list_stack.pop(); self._flush_buf(); return
        if tag == "ol" and self._list_stack:
            self._list_stack.pop()
            if self._ol_index:
                self._ol_index.pop()
            self._flush_buf(); return
        if tag == "li":
            self._flush_buf(); return
        if tag in _BLOCK_TAGS and self._cell is None:
            self._flush_buf()

    # -- table rendering ---------------------------------------------------
    @staticmethod
    def _clean_cell(text: str) -> str:
        # Normalise whitespace and keep <br> joins tidy. Pipe/emphasis escaping
        # of literal text already happened in handle_data (_escape_cell_data),
        # so markup markers (** ` etc.) are preserved here.
        text = text.replace("\n", "<br>")
        parts = [seg.strip() for seg in text.split("<br>")]
        parts = [p for p in parts if p]
        joined = "<br>".join(parts)
        if len(joined) > MAX_CELL_WIDTH:
            joined = joined[:MAX_CELL_WIDTH]
        return joined

    def _render_table(self) -> None:
        # Drop empty rows, preserving each row's header flag.
        rows = [(is_hdr, cells) for is_hdr, cells in self._rows if cells]
        if not rows:
            return
        ncols = min(max(len(cells) for _, cells in rows), MAX_TABLE_COLS)
        norm = [(is_hdr, (cells + [""] * (ncols - len(cells)))[:ncols]) for is_hdr, cells in rows]
        # Header = first row flagged with a <th>, else the first row.
        hdr_idx = next((i for i, (is_hdr, _) in enumerate(norm) if is_hdr), 0)
        header = norm[hdr_idx][1]
        body = [cells for i, (_, cells) in enumerate(norm) if i != hdr_idx]
        lines = ["| " + " | ".join(header) + " |",
                 "| " + " | ".join(["---"] * ncols) + " |"]
        for cells in body:
            lines.append("| " + " | ".join(cells) + " |")
        self.out.append("\n".join(lines))

    # -- result ------------------------------------------------------------
    def result(self) -> str:
        self._flush_buf()
        blocks = [b.strip() for b in self.out if b.strip()]
        return "\n\n".join(blocks)


def _lite_html_to_markdown(html_str: str) -> str:
    """Dependency-free stdlib fallback converter."""
    try:
        parser = _MarkdownExtractor()
        parser.feed(html_str)
        parser.close()
        return parser.result().strip()
    except Exception:
        return ""


def html_to_markdown(html_str: str) -> str:
    """Convert an HTML fragment to Markdown.

    Uses the optional ``html-to-markdown`` library when available, otherwise the
    stdlib fallback. Returns ``""`` for empty/non-HTML input or on failure.
    """
    if not html_str or "<" not in html_str:
        return ""
    if _HAVE_LIB:
        try:
            result = _lib_convert(html_str, _LibOptions(br_in_tables=True))
            content = (getattr(result, "content", None) or "").strip()
            if content:
                return content
        except Exception:
            pass  # fall through to the stdlib parser
    return _lite_html_to_markdown(html_str)
