from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

BUILTIN_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


class SkillsLoader:
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self.workspace_skills.mkdir(parents=True, exist_ok=True)

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        skills = []

        def discover(root: Path, source: str) -> None:
            if not root or not root.exists():
                return
            for skill_file in root.rglob("SKILL.md"):
                relative = skill_file.parent.relative_to(root).as_posix()
                if relative == ".":
                    continue
                if not any(s["name"] == relative for s in skills):
                    skills.append({"name": relative, "path": str(skill_file), "source": source})

        discover(self.workspace_skills, "workspace")
        discover(self.builtin_skills, "builtin")

        if filter_unavailable:
            return [
                s
                for s in skills
                if self._check_requirements(self._get_skill_config(s["name"]))
            ]
        return skills

    def route_skills(self, query: str, limit: int = 3) -> list[dict[str, str | float]]:
        """Deterministically select skills matching a request and their parents."""
        query_lower = query.lower()
        scored = []
        for skill in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(skill["name"]) or {}
            cfg = self._get_skill_config(skill["name"])
            triggers = cfg.get("triggers", []) if isinstance(cfg, dict) else []
            if isinstance(triggers, str):
                triggers = [triggers]
            haystack = " ".join([skill["name"], meta.get("description", ""), *map(str, triggers)]).lower()
            score = sum(1.0 for token in set(re.findall(r"[\w-]+", query_lower)) if token in haystack)
            score += sum(4.0 for trigger in triggers if str(trigger).lower().strip() in query_lower)
            if cfg.get("parent") and score:
                score += 0.25
            if cfg.get("always"):
                score += 0.1
            if score:
                scored.append({**skill, "score": score, "parent": str(cfg.get("parent", ""))})
        scored.sort(key=lambda item: (-float(item["score"]), str(item["name"])))
        return scored[: max(1, limit)]

    def load_routed_skills(self, query: str, limit: int = 3) -> str:
        routed = self.route_skills(query, limit=limit)
        names = [str(item["name"]) for item in routed]
        # Include parent skills so domain guidance always accompanies child skills.
        for item in routed:
            parent = str(item.get("parent", ""))
            if parent and parent not in names and not parent.startswith("Dev/"):
                candidate = f"Dev/{parent}"
                if self.load_skill(candidate):
                    parent = candidate
            if parent and parent not in names:
                names.append(parent)
        router = "Dev"
        if self.load_skill(router) and router not in names:
            names.append(router)
        return self.load_skills_for_context(names)

    def _check_requirements(self, skill_config: dict) -> bool:
        requires = skill_config.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def load_skill(self, name: str) -> str | None:
        workspace_skill = self.workspace_skills / Path(name) / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        if self.builtin_skills:
            builtin_skill = self.builtin_skills / Path(name) / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def _strip_frontmatter(self, content: str) -> str:
        if content.startswith("---"):
            match = re.match(r"^---\r?\n.*?\r?\n---\r?\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content

    def get_skill_metadata(self, name: str) -> dict | None:
        content = self.load_skill(name)
        if not content:
            return None

        if content.startswith("---"):
            match = re.match(r"^---\r?\n(.*?)\r?\n---", content, re.DOTALL)
            if match:
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip().strip("\"'")
                return metadata

        return None

    def _get_skill_config(self, name: str) -> dict:
        meta = self.get_skill_metadata(name) or {}
        return self._parse_skill_config(meta.get("metadata", ""))

    def _parse_skill_config(self, raw: str) -> dict:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            for key in ("akashic", "skill"):
                if key in data:
                    return data[key]
            return data
        except (json.JSONDecodeError, TypeError):
            return {}

    def get_always_skills(self) -> list[str]:
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_config = self._parse_skill_config(meta.get("metadata", ""))
            if skill_config.get("always") or meta.get("always"):
                result.append(s["name"])
        return result

    def build_skills_summary(self) -> str:
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_config = self._get_skill_config(s["name"])
            available = self._check_requirements(skill_config)

            source = s["source"]
            lines.append(
                f'  <skill available="{str(available).lower()}" source="{source}">'
            )
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            if not available:
                missing = self._get_missing_requirements(skill_config)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append(f"  </skill>")
        lines.append("</skills>")

        return "\n".join(lines)

    def _get_missing_requirements(self, skill_config: dict) -> str:
        missing = []
        requires = skill_config.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name

    def create_skill(self, name: str, description: str, content: str, metadata: dict | None = None) -> bool:
        try:
            skill_dir = self.workspace_skills / name
            skill_dir.mkdir(parents=True, exist_ok=True)

            metadata_str = json.dumps(metadata or {"akashic": {"always": False}})

            skill_content = f"""---
name: {name}
description: {description}
metadata: {metadata_str}
---

{content}
"""

            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text(skill_content, encoding="utf-8")
            return True
        except Exception:
            return False

    def update_skill(self, name: str, content: str) -> bool:
        try:
            workspace_skill = self.workspace_skills / name / "SKILL.md"
            if workspace_skill.exists():
                workspace_skill.write_text(content, encoding="utf-8")
                return True

            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                skill_dir = self.workspace_skills / name
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
                return True

            return False
        except Exception:
            return False

    def delete_skill(self, name: str) -> bool:
        try:
            skill_dir = self.workspace_skills / name
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
                return True
            return False
        except Exception:
            return False
