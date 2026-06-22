from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from zgraph.core.skills.loader import Skill
from zgraph.workflow.builder import WorkflowBuilder
from zgraph.workflow.spec import WorkflowSpec


@dataclass(slots=True)
class WorkflowDefinition:

    """工作流definition。"""
    name: str
    spec: WorkflowSpec
    source: Path
    skill: Skill


class WorkflowRegistry:

    """工作流注册表。"""
    def __init__(self, *, zgraph_home: Path, skills_dir: Path) -> None:
        """初始化实例属性。
            """

        self.zgraph_home = zgraph_home
        self.skills_dir = skills_dir
        self.builder = WorkflowBuilder()

    def find_for_skills(self, skills: Iterable[Skill]) -> WorkflowDefinition | None:
        """查找for技能。
        
            参数:
                skills: 技能（Iterable[Skill]）
        
            返回:
                返回类型为 WorkflowDefinition | None 的结果
            """

        for skill in skills:
            if not _is_strict_workflow_skill(skill):
                continue
            path = self._path_for_skill(skill)
            if path is None:
                continue
            spec = self.builder.spec_from_file(path)
            return WorkflowDefinition(name=spec.name, spec=spec, source=path, skill=skill)
        return None

    def _path_for_skill(self, skill: Skill) -> Path | None:
        """内部方法：路径for技能。
        
            参数:
                skill: 技能（Skill）
        
            返回:
                返回类型为 Path | None 的结果
            """

        candidates: list[Path] = []
        if skill.workflow:
            workflow = Path(skill.workflow)
            if workflow.is_absolute():
                candidates.append(workflow)
            else:
                candidates.append(self.zgraph_home / "workflows" / workflow)
                if workflow.suffix == "":
                    candidates.append(self.zgraph_home / "workflows" / f"{skill.workflow}.yaml")
                    candidates.append(self.zgraph_home / "workflows" / f"{skill.workflow}.yml")
                source_parent = Path(skill.source).parent
                candidates.append(source_parent / workflow)
                if workflow.suffix == "":
                    candidates.append(source_parent / f"{skill.workflow}.yaml")
                    candidates.append(source_parent / f"{skill.workflow}.yml")

        if skill.source != "builtin":
            candidates.append(Path(skill.source).parent / "workflow.yaml")
            candidates.append(Path(skill.source).parent / "workflow.yml")

        candidates.append(self.zgraph_home / "workflows" / f"{skill.name}.yaml")
        candidates.append(self.zgraph_home / "workflows" / f"{skill.name}.yml")

        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.expanduser()
            if resolved in seen:
                continue
            seen.add(resolved)
            if resolved.exists() and resolved.is_file():
                return resolved
        return None


def _is_strict_workflow_skill(skill: Skill) -> bool:
    """内部方法：判断是否为strict工作流技能。
    
        参数:
            skill: 技能（Skill）
    
        返回:
            返回类型为 bool 的结果
        """

    mode = skill.workflow_mode.strip().lower()
    tags = {tag.strip().lower() for tag in skill.tags}
    validations = {validation.strip().lower() for validation in skill.validations}
    return bool(skill.workflow) or mode == "strict" or "strict-workflow" in validations or "workflow" in tags
