import { openDB } from "idb";
import type { Attempt, Data, Question, Result } from "./types";
const database = () =>
  openDB("exam-studio", 1, {
    upgrade(db) {
      db.createObjectStore("state");
      db.createObjectStore("attempts", { keyPath: "id" });
    },
  });
export async function loadData(): Promise<Data | undefined> {
  return (await database()).get("state", "data");
}
export async function cacheData(data: Data) {
  await (await database()).put("state", data, "data");
}
export async function listAttempts(): Promise<Attempt[]> {
  return (await database()).getAll("attempts");
}
export async function saveAttempt(a: Attempt) {
  const db = await database();
  const tx = db.transaction(["attempts", "state"], "readwrite");
  await tx.objectStore("attempts").put(a);
  await tx.objectStore("state").put(a.id, "active");
  await tx.done;
}
export function keepKnownQuestions(a: Attempt, questionIds: Set<string>): Attempt | undefined {
  if (!Array.isArray(a.questions)) return a;
  const questions = a.questions.filter((question) => questionIds.has(question.id));
  if (!questions.length) return undefined;
  const keptIds = new Set(questions.map((question) => question.id));
  const cleanMap = <T,>(values: Record<string, T> | undefined) =>
    Object.fromEntries(
      Object.entries(values || {}).filter(([questionId]) => keptIds.has(questionId)),
    ) as Record<string, T>;
  const answers = cleanMap(a.answers),
    results = cleanMap(a.results),
    flags = cleanMap(a.flags),
    notes = cleanMap(a.notes);
  const changed =
    questions.length !== a.questions.length ||
    Object.keys(answers).length !== Object.keys(a.answers || {}).length ||
    Object.keys(results).length !== Object.keys(a.results || {}).length ||
    Object.keys(flags).length !== Object.keys(a.flags || {}).length ||
    Object.keys(notes).length !== Object.keys(a.notes || {}).length;
  return changed
    ? {
        ...a,
        questions,
        answers,
        results,
        flags,
        notes,
        index: Math.min(a.index, Math.max(0, questions.length - 1)),
        revision: a.revision + 1,
        updated_at: new Date().toISOString(),
      }
    : a;
}
export async function purgeMissingQuestions(questionIds: Set<string>) {
  const db = await database();
  const tx = db.transaction(["attempts", "state"], "readwrite");
  const attempts = await tx.objectStore("attempts").getAll();
  const activeId = await tx.objectStore("state").get("active");
  const kept: Attempt[] = [];
  for (const attempt of attempts) {
    const cleaned = keepKnownQuestions(attempt, questionIds);
    if (!cleaned) {
      await tx.objectStore("attempts").delete(attempt.id);
      if (attempt.id === activeId) await tx.objectStore("state").delete("active");
      continue;
    }
    if (cleaned !== attempt) await tx.objectStore("attempts").put(cleaned);
    kept.push(cleaned);
  }
  const active = activeId ? await tx.objectStore("attempts").get(activeId) : undefined;
  if (activeId && !active) await tx.objectStore("state").delete("active");
  await tx.done;
  return { attempts: kept, active };
}
export async function loadActive(): Promise<Attempt | undefined> {
  const db = await database();
  const id = await db.get("state", "active");
  return id ? db.get("attempts", id) : undefined;
}
export async function syncAttempts() {
  let sanitized = false;
  const db = await database();
  const activeId = await db.get("state", "active");
  for (const a of await listAttempts()) {
    const latest: Attempt = await api("/attempts/" + a.id, {
      method: "PUT",
      body: JSON.stringify(a),
    });
    const before = (a.questions || []).map((question) => question.id);
    const after = (latest.questions || []).map((question) => question.id);
    if (before.length !== after.length || before.some((id, index) => id !== after[index])) {
      sanitized = true;
      if (after.length) await db.put("attempts", latest);
      else {
        await db.delete("attempts", a.id);
        if (a.id === activeId) await db.delete("state", "active");
      }
    }
  }
  return sanitized;
}
export function newAttempt(questions: Question[], name: string): Attempt {
  return {
    id: crypto.randomUUID(),
    name,
    questions: structuredClone(questions),
    index: 0,
    answers: {},
    results: {},
    flags: {},
    notes: {},
    revision: 1,
    updated_at: new Date().toISOString(),
  };
}
export function normalized(s: string) {
  return s
    .normalize("NFKC")
    .replace(/\s/g, "")
    .toLowerCase()
    .replace(/[。.]+$/, "");
}
const numericAnswerPattern = new RegExp(
  "^([+-]?\\d[\\d,]*(?:\\.\\d+)?(?:[〜～~–−][+-]?\\d[\\d,]*(?:\\.\\d+)?)?)" +
    "((?:(?:パーセント|億円|万円|千円|営業日|年間|週間|か月間|カ月間|ヶ月間|" +
    "種類|年度|か月|カ月|ヶ月|時間|分|秒|日間|年|月|日|人|名|個|件|回|台|" +
    "本|冊|倍|点|円|ドル|km|kg|cm|mm|MB|GB|KB|TB|ml|mL|m|g|L|%|" +
    "以内|以上|以下|未満|程度|まで|強|弱))*)$",
  "i",
);
function canonicalNumericPart(value: string) {
  const normalizedValue = value.replace(/,/g, "");
  const sign = /^[+-]/.test(normalizedValue) ? normalizedValue[0] : "";
  const unsigned = sign ? normalizedValue.slice(1) : normalizedValue;
  const [wholeValue, fractionValue] = unsigned.split(".");
  const whole = wholeValue.replace(/^0+(?=\d)/, "") || "0";
  const fraction = (fractionValue || "").replace(/0+$/, "");
  return `${whole === "0" && !fraction ? "" : sign}${whole}${fraction ? `.${fraction}` : ""}`;
}
function numericAnswerKey(value: string): string | null {
  const normalizedValue = value.normalize("NFKC").replace(/\s/g, "");
  const answerParts = normalizedValue.split("/");
  if (answerParts.length > 1) {
    const keys = answerParts.map((part) => numericSingleAnswerKey(part));
    return keys.some((key) => key === null)
      ? null
      : `numbers:${keys.join("/")}`;
  }
  return numericSingleAnswerKey(normalizedValue);
}
function numericSingleAnswerKey(value: string): string | null {
  const match = value.match(numericAnswerPattern);
  if (!match) return null;
  const numberParts = match[1].split(/[〜～~–−]/);
  return `number:${numberParts.map(canonicalNumericPart).join("~")}`;
}
function comparableAnswer(value: string) {
  return numericAnswerKey(value) || normalized(value);
}
export function gradeLocal(q: Question, answer: string): Result {
  const correct =
    q.question_type === "choice"
      ? answer === q.answer
      : [q.answer, ...q.accepted_answers].some(
          (a) => comparableAnswer(a) === comparableAnswer(answer),
        );
  return {
    correct,
    score: correct ? 1 : 0,
    reason: correct
      ? "正解と一致しました。"
      : "模範解答と解説を確認しましょう。",
    reference: false,
  };
}
export async function api(path: string, options: RequestInit = {}, timeoutMs = 180000) {
  const r = await fetch("/api" + path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!r.ok) {
    let text = "通信に失敗しました";
    try {
      const body = await r.json();
      text =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {}
    throw new Error(text);
  }
  return r.json();
}
