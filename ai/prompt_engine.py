from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    safe_name = Path(name).name
    path = _PROMPT_DIR / safe_name
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {safe_name}")
    return path.read_text(encoding="utf-8").strip()


def build_instructions(feature: str = "general") -> str:
    system = load_prompt("system.md")
    feature_path = f"{feature}.md"
    try:
        specific = load_prompt(feature_path)
    except FileNotFoundError:
        specific = load_prompt("general.md")
    return f"{system}\n\nFEATURE INSTRUCTIONS\n{specific}"
