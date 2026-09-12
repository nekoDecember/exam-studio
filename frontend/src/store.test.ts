import "fake-indexeddb/auto";
import { describe, it, expect } from "vitest";
import {
  gradeLocal,
  newAttempt,
  saveAttempt,
  loadActive,
  listAttempts,
  cacheData,
  loadData,
} from "./store";
import type { Question, Data } from "./types";
const q: Question = {
  id: "q",
  body: "利益の名称",
  category: "財務",
  question_type: "word",
  choices: [],
  answer: "売上総利益",
  accepted_answers: ["粗利"],
  explanation: "解説",
  source_references: [],
  warnings: [],
  status: "active",
  favorite: false,
  note: "",
  recipe_id: "",
  question_set_id: "",
  parent: "第1問",
  score_weight: 1,
  grading_rubric: "",
};
describe("local-first practice", () => {
  it("persists answers, flags, notes and immutable question snapshot", async () => {
    const source = { ...q };
    const a = newAttempt([source], "演習");
    a.answers.q = "粗利";
    a.flags.q = true;
    a.notes.q = "覚える";
    a.results.q = gradeLocal(q, "粗利");
    await saveAttempt(a);
    source.body = "編集";
    const r = await loadActive();
    expect(r?.questions[0].body).toBe("利益の名称");
    expect(r?.answers.q).toBe("粗利");
    expect(r?.flags.q).toBe(true);
    expect(r?.notes.q).toBe("覚える");
    expect(r?.results.q.correct).toBe(true);
    expect((await listAttempts()).length).toBeGreaterThan(0);
  });
  it("normalizes accepted answers but compares choice keys exactly", () => {
    expect(gradeLocal(q, " 粗 利。").correct).toBe(true);
    expect(gradeLocal({ ...q, answer: "ABC" }, "ａｂｃ").correct).toBe(true);
    expect(
      gradeLocal({ ...q, question_type: "choice", answer: "ABC" }, "abc")
        .correct,
    ).toBe(false);
  });
  it("caches the bank for offline startup", async () => {
    await cacheData({ questions: [q] } as Data);
    expect((await loadData())?.questions[0].id).toBe("q");
  });
});
