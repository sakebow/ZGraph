from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass(slots=True)
class Skill:

    """技能"""
    name: str
    description: str
    content: str
    required_tools: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    validations: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    workflow: str = ""
    workflow_mode: str = ""
    source: str = "builtin"


BUILTIN_SKILLS: tuple[Skill, ...] = (
    Skill(
        name="file_generation",
        description="Generate or update files in the run workspace with write/update tools.",
        content="Use write/update only after guardian approval for medium or high risk tasks.",
        required_tools=["write", "read"],
        preconditions=["current-run-workspace"],
        validations=["path-inside-workspace"],
        tags=["file", "markdown", "document"],
    ),
    Skill(
        name="document_merge",
        description="Read multiple workspace files and produce one output artifact.",
        content="Read source files, summarize differences, then write a merged artifact.",
        required_tools=["read", "glob", "write"],
        preconditions=["input-files-exist"],
        validations=["merged-output-created"],
        tags=["merge", "document", "todo"],
    ),
    Skill(
        name="safe_shell_execution",
        description="Shell execution must be treated as high risk and isolated to run workspace.",
        content="Use bash only when enabled and after guardian approval.",
        required_tools=["bash"],
        preconditions=["bash-enabled"],
        validations=["capture-return-code"],
        tags=["bash", "script", "execute"],
    ),
    Skill(
        name="conversation",
        description="Answer conversational requests without side effects.",
        content="For low-risk chat, answer directly with the provider or fallback responder.",
        required_tools=[],
        preconditions=[],
        validations=["answer-complete"],
        tags=["chat", "qa", "summary"],
    ),
    Skill(
        name="question_recommendation",
        description="Recommend follow-up questions from the latest saved memory message.",
        content="Load the newest saved conversation message and use a separate conversation to return {'data': [{'message': '...'}]}.",
        required_tools=[],
        preconditions=["latest-message-available"],
        validations=["structured-recommendation-output"],
        tags=["recommend", "questions", "follow-up", "conversation"],
    ),
)


class SkillLoader:

    """技能加载器"""
    def __init__(self, skills_dirs: Path | Iterable[Path] | None = None) -> None:
        """初始化实例属性。
        
            参数:
                skills_dirs: 技能dirs，可选，默认为 None（Path | Iterable[Path] | None）
            """

        if skills_dirs is None:
            self.skills_dirs: list[Path] = []
        elif isinstance(skills_dirs, Path):
            self.skills_dirs = [skills_dirs]
        else:
            self.skills_dirs = list(skills_dirs)

    def load(self) -> list[Skill]:
        """加载技能"""

        skills = list(BUILTIN_SKILLS)
        seen_sources = {skill.source for skill in skills}
        seen_names = {skill.name.strip().lower() for skill in skills}
        for skills_dir in self.skills_dirs:
            if not skills_dir.exists():
                continue
            for skill in self._load_external(skills_dir):
                normalized_name = skill.name.strip().lower()
                if skill.source in seen_sources or normalized_name in seen_names:
                    continue
                seen_sources.add(skill.source)
                seen_names.add(normalized_name)
                skills.append(skill)
        return skills

    def _load_external(self, directory: Path) -> list[Skill]:
        """内部方法：加载external"""

        loaded: list[Skill] = []
        for path in self._iter_skill_files(directory):
            try:
                loaded.append(self._parse_skill(path))
            except Exception:
                continue
        return loaded

    def _iter_skill_files(self, directory: Path) -> list[Path]:
        """内部方法：iter技能files"""

        paths: list[Path] = []
        seen: set[Path] = set()

        for path in directory.rglob("SKILL.md"):
            resolved = path.resolve()
            seen.add(resolved)
            paths.append(path)

        for path in directory.glob("*.md"):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)

        return sorted(paths, key=lambda item: str(item).lower())

    def _parse_skill(self, path: Path) -> Skill:
        """内部方法：解析技能"""

        text = path.read_text(encoding="utf-8", errors="replace")
        metadata: dict[str, Any] = {}
        content = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                metadata = yaml.safe_load(parts[1]) or {}
                content = parts[2].strip()

        title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        name = str(metadata.get("name") or (title_match.group(1) if title_match else path.stem))
        description = str(metadata.get("description") or content.splitlines()[0:1] or "")
        if isinstance(description, list):
            description = " ".join(description)
        required_tools = _metadata_list(metadata.get("required_tools") or metadata.get("tools"))
        preconditions = _metadata_list(metadata.get("preconditions"))
        validations = _metadata_list(metadata.get("validations"))
        tags = _metadata_list(metadata.get("tags"))
        workflow = str(metadata.get("workflow") or "").strip()
        workflow_mode = str(metadata.get("workflow_mode") or "").strip().lower()
        return Skill(
            name=name,
            description=description,
            content=content,
            required_tools=required_tools,
            preconditions=preconditions,
            validations=validations,
            tags=tags,
            workflow=workflow,
            workflow_mode=workflow_mode,
            source=str(path),
        )


def _metadata_list(value: Any) -> list[str]:
    """内部方法：元数据列表"""

    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]
