import re

from whats_new.html import ReportFile, discover_reports, render_digest, render_markdown


def write(dirpath, name, body):
    p = dirpath / name
    p.write_text(body, encoding="utf-8")
    return p


def section_ids(html):
    return re.findall(r'<section id="([^"]+)"', html)


def test_discover_orders_newest_first_with_reruns_descending(tmp_path):
    for name in ["2026-10-10.md", "2026-10-11.md", "2026-10-11-2.md", "2026-10-11-10.md", "2026-10-09-3.md"]:
        write(tmp_path, name, "# What's new in AI\n")
    write(tmp_path, "notes.md", "ignored")
    write(tmp_path, "2026-10-12.txt", "ignored")
    ids = [r.run_id for r in discover_reports(tmp_path)]
    assert ids == ["2026-10-11-10", "2026-10-11-2", "2026-10-11", "2026-10-10", "2026-10-09-3"]


def test_report_file_parses_date_and_run():
    r = ReportFile.from_name("2026-10-11-3.md")
    assert (r.date, r.run, r.run_id) == ("2026-10-11", 3, "2026-10-11-3")
    base = ReportFile.from_name("2026-10-11.md")
    assert (base.run, base.run_id) == (1, "2026-10-11")
    assert ReportFile.from_name("README.md") is None


def test_digest_has_sections_toc_and_noindex(tmp_path):
    write(tmp_path, "2026-10-10.md", "# What's new in AI — 2026-10-10\n> **Headline:** headline-old\n")
    write(tmp_path, "2026-10-11.md", "# What's new in AI — 2026-10-11\n> **Headline:** headline-first\n")
    write(tmp_path, "2026-10-11-2.md", "# What's new in AI — 2026-10-11\n> **Headline:** headline-second\n")
    html = render_digest(discover_reports(tmp_path))
    assert section_ids(html) == ["2026-10-11-2", "2026-10-11", "2026-10-10"]
    assert '<meta name="robots" content="noindex,nofollow">' in html
    for rid in ["2026-10-11-2", "2026-10-11", "2026-10-10"]:
        assert f'href="#{rid}"' in html
    body = html[html.index("<section"):]
    assert body.index("headline-second") < body.index("headline-first") < body.index("headline-old")


def test_digest_with_no_reports(tmp_path):
    html = render_digest(discover_reports(tmp_path))
    assert "<html" in html and section_ids(html) == []


def test_raw_html_is_escaped():
    out = render_markdown('hello <script>alert(1)</script>\n\n<img src=x onerror="alert(1)">\n\n<div>x</div>\n')
    assert "<script" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "<img" not in out and "<div" not in out


def test_dangerous_links_are_not_rendered_as_links():
    md = (
        "[a](javascript:alert(1)) [b](JaVaScRiPt:alert(1)) [c](data:text/html;base64,xx) "
        "[d](vbscript:x) [e](file:///etc/passwd) <javascript:alert(1)> [f](/relative) "
        "![img](javascript:alert(1)) ![img2](data:image/png;base64,AAAA)"
    )
    out = render_markdown(md)
    assert "<a " not in out
    assert "<img" not in out
    assert 'href="javascript' not in out.lower()
    assert 'src="data' not in out.lower()


def test_safe_links_get_rel_and_target():
    out = render_markdown("[x](https://example.com/a?b=1&c=2) [m](mailto:me@example.com) [h](http://x.y)")
    assert out.count('rel="noopener noreferrer"') == 3
    assert 'href="https://example.com/a?b=1&amp;c=2"' in out
    assert 'href="mailto:me@example.com"' in out


def test_tables_and_strikethrough_enabled():
    out = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~\n")
    assert "<table>" in out and "<s>gone</s>" in out


def test_render_is_deterministic(tmp_path):
    write(tmp_path, "2026-10-11.md", "# What's new in AI\n> **Headline:** x\n")
    assert render_digest(discover_reports(tmp_path)) == render_digest(discover_reports(tmp_path))


def test_cli_render_html(tmp_path):
    from whats_new.cli import main

    (tmp_path / "reports").mkdir()
    write(tmp_path / "reports", "2026-10-11.md", "# What's new in AI\n<b>bold?</b>\n")
    out = tmp_path / "site" / "tok" / "index.html"
    assert main(["--root", str(tmp_path), "render-html", "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert '<section id="2026-10-11"' in html and "&lt;b&gt;bold?&lt;/b&gt;" in html
