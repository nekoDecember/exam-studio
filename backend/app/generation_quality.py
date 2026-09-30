"""Finite generation budget shared by every batch in a job."""

from dataclasses import dataclass, field


@dataclass
class GenerationBudget:
    requested_count: int
    attempted_count: int = 0
    rejected_count: int = 0
    blocked_sources: set[str] = field(default_factory=set)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    rejected_questions: list[dict] = field(default_factory=list)

    @property
    def exhausted(self):
        return self.attempted_count >= self.requested_count * 3

    def reject(self, question, reasons):
        self.rejected_count += 1
        for code in {reason["code"] for reason in reasons}:
            self.rejection_counts[code] = self.rejection_counts.get(code, 0) + 1
        # Keep only small, recent candidates in memory and model input.
        self.rejected_questions.append(
            {
                key: str(question.get(key) or "")[:320]
                for key in (
                    "body",
                    "answer",
                    "tested_concept",
                    "answer_target",
                    "question_goal",
                    "question_type",
                )
            }
        )
        self.rejected_questions = self.rejected_questions[-12:]


CHANGE_SOURCE_CODES = {
    "document_metadata",
    "visual_trivia",
    "low_learning_value",
    "unsupported_answer",
    "arbitrary_cloze",
}
