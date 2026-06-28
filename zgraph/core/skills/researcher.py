from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zgraph.core.skills.loader import Skill
from zgraph.core.tokenizer.base import BaseTokenizer, RankingDocument
from zgraph.core.tokenizer.word import WordTokenizer


@dataclass(slots=True)
class SkillMatch:
    """技能匹配。"""

    skill: Skill
    score: float
    reasons: list[str]


class SkillResearcher:
    """技能researcher。"""

    def __init__(self, skills: list[Skill], tokenizer: BaseTokenizer | None = None) -> None:
        """初始化实例属性。
        
            参数:
                skills: 技能（list[Skill]）
                tokenizer: 分词器，可选，默认为 None（BaseTokenizer | None）
            """

        self.skills = skills
        self.tokenizer = tokenizer or WordTokenizer()

    def search(self, query: str, *, top_k: int = 4, min_score: float = 0.0) -> list[SkillMatch]:
        """搜索"""

        documents = [
            RankingDocument(
                id=skill.name,
                text=" ".join(
                    [
                        skill.name,
                        skill.description,
                        " ".join(skill.tags),
                        " ".join(skill.required_tools),
                    ]
                ),
                metadata={"skill": skill},
            )
            for skill in self.skills
        ]
        ranked = self.tokenizer.rank(query, documents, top_k=max(top_k * 3, top_k), min_score=min_score)
        matches = [
            SkillMatch(
                skill=result.metadata["skill"],
                score=_score_with_local_priority(result.metadata["skill"], result.score),
                reasons=result.reasons,
            )
            for result in ranked
            if "skill" in result.metadata
        ]
        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[:top_k]


def _score_with_local_priority(skill: Skill, score: float) -> float:
    """内部方法：分数withlocalpriority"""

    source = Path(skill.source)
    if ".zgraph" in source.parts and "skills" in source.parts:
        return score + 0.25
    return score
