"""Loads SKILL.md playbooks and selects relevant skills for troubleshooting prompts."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class AgentSkill:
    """Represents a DevOps operational skill loaded from SKILL.md."""

    name: str
    description: str
    triggers: List[str] = field(default_factory=list)
    allowed_mcp_tools: List[str] = field(default_factory=list)
    instructions: str = ""
    path: str = ""


class SkillRegistry:
    """Discovers and matches skills stored in the skills directory."""

    def __init__(self, skills_dir: str) -> None:
        self.skills_dir = Path(skills_dir)
        self.skills: List[AgentSkill] = []
        self.reload()

    def reload(self) -> None:
        """Scan skills_dir for */SKILL.md and parse metadata and instructions."""
        loaded: List[AgentSkill] = []
        if not self.skills_dir.exists():
            self.skills = []
            return

        for skill_file in sorted(self.skills_dir.glob("*/SKILL.md")):
            parsed = _parse_skill_markdown(skill_file)
            if parsed:
                loaded.append(parsed)
        self.skills = loaded

    def match(self, query: str, max_skills: int = 2) -> List[AgentSkill]:
        """Match skills whose explicit triggers appear as whole terms in the user query."""
        q_lower = query.lower()
        scored = []
        for skill in self.skills:
            hits = 0
            for trigger in skill.triggers:
                t = trigger.lower().strip()
                if not t:
                    continue
                if re.search(rf"(?<![a-z0-9_]){re.escape(t)}(?![a-z0-9_])", q_lower):
                    hits += 2 if " " in t or "-" in t or "/" in t else 1
            if hits > 0:
                scored.append((hits, skill))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [s for _, s in scored[:max_skills]]


def _parse_skill_markdown(path: Path) -> AgentSkill | None:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return AgentSkill(
            name=path.parent.name,
            description="Custom DevOps skill",
            instructions=raw.strip(),
            path=str(path),
        )

    parts = raw.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = parts[1].strip()
    body = parts[2].strip()
    meta = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()

    triggers = _parse_list(meta.get("triggers", ""))
    allowed_tools = _parse_list(meta.get("allowed_mcp_tools", ""))

    return AgentSkill(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        triggers=triggers,
        allowed_mcp_tools=allowed_tools,
        instructions=body,
        path=str(path),
    )


def _parse_list(raw_val: str) -> List[str]:
    cleaned = raw_val.strip().strip("[]")
    if not cleaned:
        return []
    return [item.strip().strip("'\"") for item in cleaned.split(",") if item.strip()]
