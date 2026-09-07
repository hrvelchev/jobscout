"""Settings (env) + human-editable markdown config files.

The markdown format is deliberately forgiving - the owner edits these by hand:
everything above the first `---` line is documentation and skipped; `## heading`
starts a section; `- item` lines are entries. Sections whose heading ends in
`_considered_and_skipped` are parsed but discarded (a way to park ideas).
"""

from __future__ import annotations

from pathlib import Path

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

log = structlog.get_logger()


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_owner_id: int = 0

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "jobscout"
    postgres_password: str = "change-me"
    postgres_db: str = "jobscout"

    max_llm_calls_per_day: int = 40
    max_scored_per_run: int = 25
    devbg_pages: int = 1  # listing pages per category; >1 only for backfill
    draft_threshold: int = 70
    digest_top_n: int = 5
    digest_hour: int = 8
    digest_minute: int = 30
    scout_hour: int = 7
    scout_minute: int = 45
    timezone: str = "Europe/Sofia"

    log_level: str = "INFO"
    config_dir: Path = Path("config")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def load_settings() -> Settings:
    return Settings()


# --- markdown config parsing -------------------------------------------------


def parse_tagged_items(text: str) -> list[tuple[str, str]]:
    """[(section_tag, item), ...] - see module docstring for the format."""
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "---") + 1
    except StopIteration:
        start = 0
    items: list[tuple[str, str]] = []
    tag = ""
    skipping = False
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            tag = stripped[3:].strip().lower()
            skipping = tag.endswith("_considered_and_skipped")
            continue
        if skipping or not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        if item:
            items.append((tag, item))
    return items


def _read_config(config_dir: Path, stem: str, suffix: str = ".md") -> str:
    """Real file wins; fall back to the committed example (with a visible log
    line, so running on the fictional persona is never silent)."""
    real = config_dir / f"{stem}{suffix}"
    if real.exists():
        return real.read_text(encoding="utf-8")
    example = config_dir / f"{stem}.example{suffix}"
    log.warning("config_using_example", file=str(example))
    return example.read_text(encoding="utf-8")


def _section(items: list[tuple[str, str]], tag: str) -> list[str]:
    return [item for t, item in items if t == tag]


def _kv_section(items: list[tuple[str, str]], tag: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in _section(items, tag):
        if "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        out[key.strip().lower()] = value.strip()
    return out


def load_filters(config_dir: Path) -> tuple[list[str], list[str]]:
    """(exclude_patterns, required_keywords), lowercased."""
    items = parse_tagged_items(_read_config(config_dir, "filters"))
    return (
        [p.lower() for p in _section(items, "exclude_patterns")],
        [k.lower() for k in _section(items, "required_keywords")],
    )


def load_lanes(config_dir: Path) -> tuple[dict[str, int], dict[str, str], list[str]]:
    """(lane_weights, lane_cv_map, dream_companies)."""
    items = parse_tagged_items(_read_config(config_dir, "lanes"))
    weights = {}
    for lane, raw in _kv_section(items, "lane_weights").items():
        try:
            weights[lane] = int(raw)
        except ValueError:
            log.warning("config_bad_lane_weight", lane=lane, value=raw)
    cv_map = _kv_section(items, "lane_cv_map")
    dreams = [d.lower() for d in _section(items, "dream_companies")]
    return weights, cv_map, dreams


def load_sources(config_dir: Path) -> tuple[list[str], list[str]]:
    """(devbg_categories, greenhouse_boards)."""
    items = parse_tagged_items(_read_config(config_dir, "sources"))
    return _section(items, "devbg_categories"), _section(items, "greenhouse_boards")


def load_profile(config_dir: Path) -> str:
    """The candidate profile injected into the scoring prompt, verbatim."""
    return _read_config(config_dir, "profile")


def load_notes_examples(config_dir: Path, limit: int = 5) -> str:
    """Few-shot corpus for draft generation: real sent notes (gitignored)."""
    notes_dir = config_dir / "notes"
    if not notes_dir.exists():
        return ""
    blocks = []
    for path in sorted(notes_dir.glob("*.txt"))[:limit]:
        blocks.append(
            f"--- example note ({path.stem}) ---\n{path.read_text(encoding='utf-8').strip()}"
        )
    return "\n\n".join(blocks)
