import json
from datetime import timedelta

import pytest

from whats_new.state import NothingToCommit, Paths, SeenStore, commit, load_candidates, read_since, write_candidates
from whats_new.text import normalize_url, parse_iso


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path)


def test_since_defaults_to_48h_then_reads_last_success(paths, now):
    assert read_since(paths, now) == now - timedelta(hours=48)
    paths.state_dir.mkdir()
    paths.last_success.write_text("2026-10-10T07:30:00Z\n")
    assert read_since(paths, now) == parse_iso("2026-10-10T07:30:00Z")
    paths.last_success.write_text("not a date")
    assert read_since(paths, now) == now - timedelta(hours=48)


def test_seen_store_normalizes_and_ignores_duplicates(paths, now):
    with SeenStore(paths.seen_db) as seen:
        assert seen.add(["https://a.com/x/?utm_source=1", "https://a.com/x"], now) == 1
        assert seen.seen_among(["https://a.com/x#frag", "https://a.com/y"]) == {"https://a.com/x"}
        assert seen.count() == 1


def test_write_candidates_is_atomic_and_leaves_no_temp_files(paths):
    write_candidates(paths, {"candidates": [], "generated_at": "2026-10-11T10:30:00Z"})
    assert json.loads(paths.candidates.read_text())["candidates"] == []
    assert [p.name for p in paths.state_dir.iterdir()] == ["candidates.json"]


def test_corrupt_candidates_are_moved_aside(paths):
    paths.state_dir.mkdir()
    paths.candidates.write_text("{truncated")
    assert load_candidates(paths) is None
    assert not paths.candidates.exists()
    assert any(p.name.startswith("candidates.corrupt-") for p in paths.state_dir.iterdir())


def test_commit_marks_seen_advances_last_success_and_deletes_candidates(paths, now):
    write_candidates(
        paths,
        {
            "generated_at": "2026-10-11T10:30:00Z",
            "since": "2026-10-10T10:30:00Z",
            "sources_health": [],
            "candidates": [{"url": "https://a.com/1/?utm_source=x"}, {"url": "https://a.com/2"}],
        },
    )
    added, stamp = commit(paths, now + timedelta(hours=1))
    assert added == 2
    assert stamp == "2026-10-11T10:30:00Z"
    assert paths.last_success.read_text().strip() == "2026-10-11T10:30:00Z"
    assert not paths.candidates.exists()
    with SeenStore(paths.seen_db) as seen:
        assert seen.seen_among(["https://a.com/1", "https://a.com/2"]) == {
            normalize_url("https://a.com/1"),
            "https://a.com/2",
        }


def test_commit_without_candidates_raises(paths, now):
    with pytest.raises(NothingToCommit):
        commit(paths, now)
