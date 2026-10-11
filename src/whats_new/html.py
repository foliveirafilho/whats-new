"""Render every reports/*.md into one self-contained HTML digest (newest first).

The reports are written from untrusted web content and the digest is published on a public
(if unlisted) GitHub Pages URL, so rendering is locked down:
- raw HTML in the markdown is escaped, never passed through (`html: False`);
- links and images are only kept for http(s) (links also mailto); anything else stays as text;
- every link gets rel="noopener noreferrer" and opens in a new tab.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt

_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.md$")
_LINK_SCHEMES = ("http://", "https://", "mailto:")
_IMAGE_SCHEMES = ("http://", "https://")
_HEADLINE_RE = re.compile(r"^>\s*\*\*Headline:\*\*\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class ReportFile:
    path: Path
    date: str
    run: int  # 1 for YYYY-MM-DD.md, n for YYYY-MM-DD-n.md

    @property
    def run_id(self) -> str:
        return self.date if self.run == 1 else f"{self.date}-{self.run}"

    @classmethod
    def from_name(cls, name: str, directory: Path = Path(".")) -> ReportFile | None:
        m = _NAME_RE.match(name)
        if not m:
            return None
        return cls(directory / name, m.group(1), int(m.group(2) or 1))


def discover_reports(reports_dir: Path) -> list[ReportFile]:
    """All YYYY-MM-DD[-n].md reports, newest first (date desc, then run desc)."""
    found = []
    if reports_dir.is_dir():
        for p in reports_dir.iterdir():
            r = ReportFile.from_name(p.name, reports_dir) if p.is_file() else None
            if r:
                found.append(r)
    return sorted(found, key=lambda r: (r.date, r.run), reverse=True)


def _make_md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])

    def validate_link(url: str) -> bool:
        return url.strip().lower().startswith(_LINK_SCHEMES)

    md.validateLink = validate_link

    def link_open(tokens, idx, options, env):
        tokens[idx].attrSet("rel", "noopener noreferrer")
        tokens[idx].attrSet("target", "_blank")
        return md.renderer.renderToken(tokens, idx, options, env)

    default_image = md.renderer.rules["image"]

    def image(tokens, idx, options, env):
        src = tokens[idx].attrGet("src") or ""
        if not str(src).strip().lower().startswith(_IMAGE_SCHEMES):
            alt = md.renderer.renderInlineAsText(tokens[idx].children or [], options, env)
            return _html.escape(alt)
        tokens[idx].attrSet("referrerpolicy", "no-referrer")
        tokens[idx].attrSet("loading", "lazy")
        return default_image(tokens, idx, options, env)

    md.renderer.rules["link_open"] = link_open
    md.renderer.rules["image"] = image
    return md


_MD = _make_md()


def render_markdown(text: str) -> str:
    return _MD.render(text)


def _headline(text: str) -> str:
    m = _HEADLINE_RE.search(text)
    if not m:
        return ""
    # Drop markdown emphasis/link syntax for the plain-text TOC entry.
    plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", m.group(1))
    return re.sub(r"[*_`]", "", plain).strip()


_CSS = """
:root{color-scheme:light dark;--bg:#fbfbfa;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--card:#fff;--accent:#2457c5;--code:#f2f2ef}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#e8e8ea;--muted:#9a9aa2;--line:#2c2c31;--card:#1b1b1e;--accent:#8ab4ff;--code:#26262b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:46rem;margin:0 auto;padding:2rem 1.25rem 4rem}
a{color:var(--accent);text-underline-offset:2px}
h1{font-size:1.6rem;line-height:1.25;margin:.2rem 0 1rem}
h2{font-size:1.25rem;margin:2rem 0 .6rem;padding-bottom:.25rem;border-bottom:1px solid var(--line)}
h3{font-size:1.08rem;margin:1.6rem 0 .4rem}
blockquote{margin:1rem 0;padding:.6rem 1rem;border-left:3px solid var(--accent);background:var(--code);border-radius:4px}
blockquote p{margin:.2rem 0}
code{background:var(--code);padding:.1em .35em;border-radius:4px;font-size:.9em}
pre{background:var(--code);padding:.9rem 1rem;border-radius:6px;overflow-x:auto;font-size:.86rem;line-height:1.5}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;font-size:.92rem;margin:1rem 0;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:.4rem .6rem;text-align:left;vertical-align:top}
img{max-width:100%}
hr{border:0;border-top:1px solid var(--line);margin:2rem 0}
.top h1{font-size:1.9rem;margin-bottom:.3rem}
.muted{color:var(--muted);font-size:.88rem}
nav.toc{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.8rem 1.1rem;margin:1.2rem 0 2rem}
nav.toc ol{margin:.3rem 0 0;padding-left:1.2rem}
nav.toc li{margin:.25rem 0}
nav.toc .hl{color:var(--muted);font-size:.88rem}
section.run{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.2rem 1.4rem;margin:0 0 2.5rem}
.run-head{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;font-size:.85rem;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:.4rem;margin-bottom:.8rem}
.run-head a{color:var(--muted);text-decoration:none}
"""


def render_digest(reports: list[ReportFile]) -> str:
    """Deterministic: same reports in -> byte-identical page out (so publishing only commits real changes)."""
    esc = _html.escape
    toc_items, sections = [], []
    for r in reports:
        text = r.path.read_text(encoding="utf-8")
        label = r.date if r.run == 1 else f"{r.date} · run {r.run}"
        hl = _headline(text)
        toc_items.append(
            f'<li><a href="#{r.run_id}">{esc(label)}</a>'
            + (f' <span class="hl">— {esc(hl)}</span>' if hl else "")
            + "</li>"
        )
        sections.append(
            f'<section id="{r.run_id}" class="run">\n'
            f'<div class="run-head"><span>{esc(label)}</span><a href="#{r.run_id}">#</a> <a href="#top">top ↑</a></div>\n'
            f"{render_markdown(text)}</section>"
        )
    toc = (
        f'<nav class="toc"><strong>Runs ({len(reports)})</strong><ol>{"".join(toc_items)}</ol></nav>'
        if reports
        else '<p class="muted">No reports yet.</p>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<title>What's new in AI — digest</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header class="top" id="top">
<h1>What's new in AI</h1>
<div class="muted">All runs, newest first{(" · latest: " + esc(reports[0].run_id)) if reports else ""}</div>
</header>
{toc}
{chr(10).join(sections)}
</main>
</body>
</html>
"""


def write_digest(reports_dir: Path, out: Path) -> int:
    reports = discover_reports(reports_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(render_digest(reports), encoding="utf-8")
    tmp.replace(out)
    return len(reports)
