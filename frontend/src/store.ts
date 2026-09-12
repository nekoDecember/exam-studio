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
export async function loadActive(): Promise<Attempt | undefined> {
  const db = await database();
  const id = await db.get("state", "active");
  return id ? db.get("attempts", id) : undefined;
}
export async function syncAttempts() {
  for (const a of await listAttempts()) {
    await api("/attempts/" + a.id, { method: "PUT", body: JSON.stringify(a) });
  }
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
export function gradeLocal(q: Question, answer: string): Result {
  const correct =
    q.question_type === "choice"
      ? answer === q.answer
      : [q.answer, ...q.accepted_answers].some(
          (a) => normalized(a) === normalized(answer),
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
export async function api(path: string, options: RequestInit = {}) {
  const r = await fetch("/api" + path, {
    ...options,
    headers:
      options.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" },
    signal: AbortSignal.timeout(180000),
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
