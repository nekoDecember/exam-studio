from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Question(BaseModel):
    id: str = ""
    body: str = Field(min_length=1, max_length=12000)
    category: str = "未分類"
    question_type: Literal["choice", "blank", "word", "short"] = "choice"
    choices: list[str] = []
    answer: str = Field(min_length=1)
    accepted_answers: list[str] = []
    explanation: str = ""
    source_references: list[dict] = []
    warnings: list[str] = []
    status: Literal["active", "deleted"] = "active"
    favorite: bool = False
    note: str = ""
    recipe_id: str = ""
    question_set_id: str = ""
    parent: str = "第1問"
    score_weight: float = Field(default=1, gt=0, le=100)
    grading_rubric: str = ""

    @model_validator(mode="after")
    def validate_choice(self):
        if self.question_type == "choice" and (
            len(self.choices) < 2
            or self.answer not in self.choices
            or len(set(self.choices)) != len(self.choices)
        ):
            raise ValueError(
                "選択問題には重複しない2個以上の選択肢と、その中に含まれる正解が必要です"
            )
        return self


class Recipe(BaseModel):
    id: str = ""
    name: str = Field(min_length=1)
    category: str
    reference_past_question_id: str = ""
    question_type: Literal["choice", "blank", "word", "short"] = "choice"
    major_count: int = Field(default=1, ge=1, le=5)
    sub_count: int = Field(default=3, ge=1, le=10)
    body_length: int = Field(default=150, ge=20, le=2000)
    choice_count: int = Field(default=4, ge=2, le=8)
    blank_count: int = Field(default=1, ge=1, le=8)
    word_bank: bool = False
    answer_type: str = "単一解答"
    difficulty: str = "標準"
    score_weight: float = Field(default=1, gt=0, le=100)
    generation_instruction: str = ""
    material_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("material_ids")
    @classmethod
    def validate_material_ids(cls, value):
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("資料IDは空にできません")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("同じ資料を重複して選択できません")
        return cleaned


class Generation(BaseModel):
    recipe_id: str
    count: int = Field(default=1, ge=1, le=5)
    material_ids: list[str] | None = Field(default=None, max_length=100)

    @field_validator("material_ids")
    @classmethod
    def validate_material_ids(cls, value):
        if value is None:
            return value
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("使用資料の指定が不正です")
        return cleaned


class Grade(BaseModel):
    question: Question
    answer: str = Field(max_length=12000)
