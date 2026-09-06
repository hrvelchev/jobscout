from jobscout.config import (
    load_filters,
    load_lanes,
    load_notes_examples,
    load_profile,
    load_sources,
    parse_tagged_items,
)

SAMPLE = """# Docs up here are ignored
- this bullet is documentation, not data

---

## exclude_patterns

- php developer
- internship

## ideas_considered_and_skipped

- crypto jobs

## required_keywords

- python
"""


def test_parse_skips_documentation_above_separator():
    items = parse_tagged_items(SAMPLE)
    assert ("", "this bullet is documentation, not data") not in items


def test_parse_sections_and_items():
    items = parse_tagged_items(SAMPLE)
    assert ("exclude_patterns", "php developer") in items
    assert ("required_keywords", "python") in items


def test_parse_skipped_sections_are_discarded():
    items = parse_tagged_items(SAMPLE)
    assert all(tag != "ideas_considered_and_skipped" for tag, _ in items)


def test_parse_without_separator_parses_whole_text():
    items = parse_tagged_items("## a\n\n- one\n")
    assert items == [("a", "one")]


def test_example_filters_load(config_dir):
    excludes, keywords = load_filters(config_dir)
    assert "php developer" in excludes
    assert "python" in keywords


def test_example_lanes_load(config_dir):
    weights, cv_map, dreams = load_lanes(config_dir)
    assert weights["ai"] == 5
    assert cv_map["data"].endswith(".pdf")
    assert "exampletech" in dreams


def test_example_sources_load(config_dir):
    devbg, greenhouse = load_sources(config_dir)
    assert "python" in devbg
    assert "examplecompany" in greenhouse


def test_example_profile_and_notes_load(config_dir):
    assert "Alex Petrov" in load_profile(config_dir)
    notes = load_notes_examples(config_dir)
    assert "example note" in notes and "Alex Petrov" in notes
