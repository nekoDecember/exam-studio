import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BookOpen,
  LayoutDashboard,
  Library,
  Files,
  SlidersHorizontal,
  ChartNoAxesCombined,
  ArrowRight,
  ArrowLeft,
  Check,
  Flag,
  Star,
  Plus,
  Search,
  RotateCcw,
  Upload,
  Cloud,
  WifiOff,
  Sparkles,
  ChevronRight,
  X,
  Trash2,
  Copy,
  Download,
  GraduationCap,
  Play,
  Settings2,
} from "lucide-react";
import {
  api,
  cacheData,
  loadData,
  loadActive,
  listAttempts,
  keepKnownQuestions,
  purgeMissingQuestions,
  saveAttempt,
  syncAttempts,
  newAttempt,
  gradeLocal,
} from "./store";
import type { Data, Question, Attempt, Recipe, Doc, GenerationJob } from "./types";
import { typeNames } from "./types";
import "./style.css";

type Page =
  | "home"
  | "practice"
  | "bank"
  | "materials"
  | "recipes"
  | "analytics";
type AnalysisMethod = "standard" | "multimodal";
type ImportItem = {
  id: string;
  name: string;
  file?: File;
  url?: string;
  status: "waiting" | "reading" | "done" | "error";
  error?: string;
};
const empty: Data = {
  questions: [],
  materials: [],
  recipes: [],
  categories: [],
  sets: [],
  provider: "mock",
};
const nav: [Page, string, typeof BookOpen][] = [
  ["home", "ダッシュボード", LayoutDashboard],
  ["practice", "演習する", BookOpen],
  ["bank", "問題バンク", Library],
  ["materials", "試験範囲の資料", Files],
  ["analytics", "学習の記録", ChartNoAxesCombined],
];
function documentAnalysisLabel(doc: Doc) {
  if (doc.analysis_method === "multimodal")
    return "LLMマルチモーダル（PDF画像優先）";
  if (doc.analysis_provider === "openai" || doc.analysis_method === "openai")
    return "標準抽出（PyMuPDF） + AI解析";
  return "標準抽出（PyMuPDF + ローカルOCR）";
}
function sourceLineLabel(fileType?: string, lineBasis?: string, ocr?: number) {
  return lineBasis === "extracted" ||
    [".html", ".htm", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".webp"].includes(fileType || "") ||
    ocr !== undefined
    ? "抽出行"
    : "行";
}
function App() {
  const [page, setPage] = useState<Page>("home"),
    [data, setData] = useState<Data>(empty),
    [attempt, setAttempt] = useState<Attempt>(),
    [history, setHistory] = useState<Attempt[]>([]),
    [status, setStatus] = useState("読み込み中"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [ready, setReady] = useState(false);
  const [search, setSearch] = useState(""),
    [cat, setCat] = useState("すべて"),
    [typ, setTyp] = useState("すべて"),
    [filter, setFilter] = useState("active"),
    [setFilterId, setSetFilterId] = useState("すべて"),
    [focusQuestionSetIds, setFocusQuestionSetIds] = useState<string[] | null>(null),
    [majorFilter, setMajorFilter] = useState("すべて"),
    [editor, setEditor] = useState<Question | null>(null),
    [doc, setDoc] = useState<Doc | null>(null),
    [recipe, setRecipe] = useState<Recipe | null>(null),
    [batch, setBatch] = useState(1),
    [analysisMethod, setAnalysisMethod] =
      useState<AnalysisMethod>("standard"),
    [urlInput, setUrlInput] = useState(""),
    [imports, setImports] = useState<ImportItem[]>([]),
    [selectedMaterialIds, setSelectedMaterialIds] = useState<string[] | null>(null),
    [quickType, setQuickType] = useState<Question["question_type"]>("choice"),
    [quickCount, setQuickCount] = useState(5),
    [generationJob, setGenerationJob] = useState<GenerationJob>(),
    [quickDifficulty, setQuickDifficulty] = useState("標準");
  const current = useRef<Attempt | undefined>(undefined),
    writeQueue = useRef(Promise.resolve()),
    generation = useRef(0),
    syncTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const perform = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "処理に失敗しました");
    } finally {
      setBusy(false);
    }
  };
  async function refresh() {
    const d = await api("/bootstrap");
    const questionIds = new Set<string>(
      d.questions.map((question: Question) => question.id),
    );
    const local = await purgeMissingQuestions(
      questionIds,
    );
    const activeAttempt = current.current
      ? keepKnownQuestions(current.current, questionIds)
      : local.active;
    if (current.current && activeAttempt && activeAttempt !== current.current)
      await saveAttempt(activeAttempt);
    const attempts = new Map(local.attempts.map((item) => [item.id, item]));
    if (
      activeAttempt &&
      (!attempts.has(activeAttempt.id) ||
        (attempts.get(activeAttempt.id)?.revision || 0) < activeAttempt.revision)
    )
      attempts.set(activeAttempt.id, activeAttempt);
    setData(d);
    await cacheData(d);
    setHistory([...attempts.values()]);
    current.current = activeAttempt;
    setAttempt(activeAttempt);
    if (!activeAttempt && page === "practice") setPage("home");
  }
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [d, a, h] = await Promise.all([
          loadData(),
          loadActive(),
          listAttempts(),
        ]);
        if (!live) return;
        if (d) setData(d);
        if (a) {
          setAttempt(a);
          current.current = a;
        }
        setHistory(h);
        setStatus("端末に保存済み");
        setReady(true);
        try {
          await refresh();
          await syncAttempts();
          if (live) setStatus("同期済み");
        } catch {
          if (live) setStatus("オフライン・端末に保存");
        }
        try {
          const jobs: GenerationJob[] = await api("/generation-jobs");
          if (live && jobs.length) {
            setGenerationJob(
              jobs.find((job) => job.status === "queued" || job.status === "running") || jobs[0],
            );
          }
        } catch {
          // Existing offline data remains usable when the generation API is unavailable.
        }
      } catch {
        if (live)
          setError(
            "端末保存を利用できません。ブラウザのストレージ設定を確認してください。",
          );
      } finally {
        if (live) setReady(true);
      }
    })();
    const sync = async () => {
      try {
        if (await syncAttempts()) await refresh();
        setStatus("同期済み");
      } catch {
        setStatus("オフライン・端末に保存");
      }
    };
    const resumeGeneration = async () => {
      try {
        const jobs: GenerationJob[] = await api("/generation-jobs");
        if (live && jobs.length) {
          setGenerationJob(
            jobs.find((job) => job.status === "queued" || job.status === "running") || jobs[0],
          );
        }
        await refresh();
      } catch {
        // Keep using cached study data until the API is reachable again.
      }
    };
    window.addEventListener("online", sync);
    window.addEventListener("online", resumeGeneration);
    const interval = setInterval(sync, 15000);
    return () => {
      live = false;
      clearInterval(interval);
      window.removeEventListener("online", sync);
      window.removeEventListener("online", resumeGeneration);
    };
  }, []);
  useEffect(() => {
    if (!generationJob || !["queued", "running"].includes(generationJob.status))
      return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const latest: GenerationJob = await api(`/generation-jobs/${generationJob.id}`);
        if (!live) return;
        if (latest.status === "complete" || latest.status === "failed") {
          try {
            await refresh();
          } catch {
            setError(
              latest.status === "complete"
                ? "生成は完了しましたが、問題一覧を更新できませんでした。再読み込みしてください。"
                : "生成は中断されました。保存済みの問題一覧を更新できませんでした。再読み込みしてください。",
            );
          }
          if (live) setGenerationJob(latest);
          return;
        }
        setGenerationJob(latest);
        timer = setTimeout(poll, 1200);
      } catch {
        if (live) timer = setTimeout(poll, 3000);
      }
    };
    poll();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [generationJob?.id, generationJob?.status]);
  useEffect(() => {
    if (page === "practice" && !attempt?.questions.length) setPage("home");
  }, [page, attempt?.questions.length]);
  function persist(next: Attempt) {
    current.current = next;
    setAttempt(next);
    const version = ++generation.current;
    setStatus("保存中…");
    writeQueue.current = writeQueue.current
      .then(async () => {
        await saveAttempt(next);
        setHistory((h) => [...h.filter((a) => a.id !== next.id), next]);
        if (version === generation.current) setStatus("端末に保存済み");
        clearTimeout(syncTimer.current);
        syncTimer.current = setTimeout(
          () =>
            syncAttempts()
              .then(async (sanitized) => {
                if (sanitized) await refresh();
                if (version === generation.current) setStatus("同期済み");
              })
              .catch(() => {
                if (version === generation.current)
                  setStatus("オフライン・端末に保存");
              }),
          700,
        );
      })
      .catch(() => {
        setStatus("保存失敗");
        setError(
          "端末に保存できません。容量・ブラウザ設定を確認してください。",
        );
      });
  }
  function patch(changes: Partial<Attempt>) {
    const a = current.current;
    if (a)
      persist({
        ...a,
        ...changes,
        revision: a.revision + 1,
        updated_at: new Date().toISOString(),
      });
  }
  const active = data.questions.filter((q) => q.status === "active");
  const results = history.flatMap((a) =>
    a.questions
      .filter((q) => a.results[q.id])
      .map((q) => ({ q, r: a.results[q.id], date: a.updated_at })),
  );
  const scored = results.filter((x) => x.r.correct !== null),
    correct = scored.filter((x) => x.r.correct).length;
  const latest = new Map<
    string,
    { q: Question; r: Attempt["results"][string]; date: string }
  >();
  results
    .sort((a, b) => a.date.localeCompare(b.date))
    .forEach((x) => latest.set(x.q.id, x));
  const wrongIds = new Set(
    [...latest.values()]
      .filter((x) => x.r.correct === false)
      .map((x) => x.q.id),
  );
  function start(questions = active, name = "すべての問題") {
    if (!questions.length) {
      setError("対象の問題がありません。問題バンクで条件を変更してください。");
      return;
    }
    persist(newAttempt(questions, name));
    setPage("practice");
  }
  function go(p: Page) {
    setPage(p);
    setError("");
    setSearch("");
    setCat("すべて");
    setTyp("すべて");
    setFilter("active");
    setSetFilterId("すべて");
    setFocusQuestionSetIds(null);
    setMajorFilter("すべて");
  }
  const categories = [
    ...new Set([
      ...data.categories.map((c) => c.name),
      ...data.questions.map((q) => q.category),
      ...data.materials.flatMap((m) =>
        m.chunks.flatMap((c) => c.categories || []),
      ),
    ]),
  ];
  const questionMatchesSetFilter = (question: Question) =>
    setFilterId === "job"
      ? !!focusQuestionSetIds?.includes(question.question_set_id)
      : setFilterId === "すべて" || question.question_set_id === setFilterId;
  const filtered = data.questions.filter(
    (q) =>
      questionMatchesSetFilter(q) &&
      (majorFilter === "すべて" || q.parent === majorFilter) &&
      q.status !== "deleted" &&
      (cat === "すべて" || q.category === cat) &&
      (typ === "すべて" || q.question_type === typ) &&
      (filter !== "favorite" || q.favorite) &&
      (filter !== "wrong" || wrongIds.has(q.id)) &&
      (q.body + q.category).toLowerCase().includes(search.toLowerCase()),
  );
  const q = attempt?.questions[attempt.index],
    result = q ? attempt?.results[q.id] : undefined;
  function navigate(delta: number) {
    if (attempt)
      patch({
        index: Math.max(
          0,
          Math.min(attempt.questions.length - 1, attempt.index + delta),
        ),
      });
  }
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (
        page !== "practice" ||
        /INPUT|TEXTAREA|SELECT/.test((e.target as HTMLElement).tagName) ||
        editor ||
        doc ||
        recipe
      )
        return;
      if (e.key === "ArrowRight") navigate(1);
      if (e.key === "ArrowLeft") navigate(-1);
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  });
  async function grade() {
    if (!q || !attempt) return;
    const id = attempt.id;
    const answer = attempt.answers[q.id] || "";
    const r =
      q.question_type === "short"
        ? await api("/grade", {
            method: "POST",
            body: JSON.stringify({ question: q, answer }),
          })
        : gradeLocal(q, answer);
    if (current.current?.id === id)
      patch({ results: { ...current.current.results, [q.id]: r } });
  }
  async function saveQ(value: Question) {
    const saved: Question = await api("/questions" + (value.id ? "/" + value.id : ""), {
      method: value.id ? "PUT" : "POST",
      body: JSON.stringify(value),
    });
    const session = current.current;
    const oldSnapshot = session?.questions.find((question) => question.id === saved.id);
    if (session && oldSnapshot) {
      const gradingFields: (keyof Question)[] = [
        "body",
        "question_type",
        "choices",
        "answer",
        "accepted_answers",
        "explanation",
        "grading_rubric",
      ];
      const answerChanged = gradingFields.some(
        (field) => JSON.stringify(oldSnapshot[field]) !== JSON.stringify(saved[field]),
      );
      const answers = { ...session.answers };
      const results = { ...session.results };
      if (answerChanged) {
        delete answers[saved.id];
        delete results[saved.id];
      }
      persist({
        ...session,
        questions: session.questions.map((question) =>
          question.id === saved.id ? saved : question,
        ),
        answers,
        results,
        revision: session.revision + 1,
        updated_at: new Date().toISOString(),
      });
    }
    setEditor(null);
    await refresh();
  }
  async function deleteQuestion(id: string) {
    await api(`/questions/${id}`, { method: "DELETE" });
    await refresh();
  }
  function freshQ(): Question {
    return {
      id: "",
      body: "",
      category: categories[0] || "未分類",
      question_type: "choice",
      choices: ["", "", "", ""],
      answer: "",
      accepted_answers: [],
      explanation: "",
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
  }
  function freshRecipe(): Recipe {
    const initialCategory = categories[0] || "未分類";
    return {
      id: "",
      name: "新しい出題レシピ",
      category: initialCategory,
      question_type: "choice",
      major_count: 1,
      sub_count: 3,
      body_length: 150,
      choice_count: 4,
      blank_count: 1,
      word_bank: false,
      answer_type: "単一解答",
      difficulty: "標準",
      score_weight: 1,
      generation_instruction: "",
      material_ids: data.materials
        .filter((m) =>
          m.chunks.some((c) =>
            (c.categories || m.categories).includes(initialCategory),
          ),
        )
        .map((m) => m.id),
    };
  }
  async function runImports(items: ImportItem[]) {
    setImports((current) => [...current.filter((entry) => !items.some((item) => item.id === entry.id)), ...items]);
    for (const item of items) {
      setImports((current) => current.map((entry) =>
        entry.id === item.id ? { ...entry, status: "reading", error: "" } : entry,
      ));
      try {
        if (item.file) {
          const body = new FormData();
          body.append("file", item.file);
          body.append("analysis_method", item.file.name.toLowerCase().endsWith(".pdf") ? analysisMethod : "standard");
          await api("/uploads", { method: "POST", body }, 600000);
        } else {
          await api("/urls", {
            method: "POST",
            body: JSON.stringify({ url: item.url }),
          }, 600000);
        }
        setImports((current) => current.map((entry) =>
          entry.id === item.id ? { ...entry, status: "done" } : entry,
        ));
        try {
          await refresh();
        } catch {
          setError("資料は保存されましたが一覧を更新できませんでした。画面を再読み込みしてください。");
        }
      } catch (cause) {
        setImports((current) => current.map((entry) =>
          entry.id === item.id
            ? { ...entry, status: "error", error: cause instanceof Error ? cause.message : "読み込めませんでした" }
            : entry,
        ));
      }
    }
  }
  function fileItems(files: FileList | File[]) {
    return Array.from(files).map((file) => ({
      id: crypto.randomUUID(), name: file.name, file, status: "waiting" as const,
    }));
  }
  async function quickGenerate() {
    const material_ids = selectedMaterialIds ?? data.materials.map((item) => item.id);
    if (!material_ids.length) throw new Error("問題に使う資料を選んでください");
    if (!Number.isInteger(quickCount) || quickCount < 1 || quickCount > 1000)
      throw new Error("問数は1〜1,000の範囲で指定してください");
    const job: GenerationJob = await api("/generation-jobs/direct", {
      method: "POST",
      body: JSON.stringify({
        material_ids,
        question_type: quickType,
        question_count: quickCount,
        difficulty: quickDifficulty,
      }),
    });
    setGenerationJob(job);
  }
  const generationRunning =
    generationJob?.status === "queued" || generationJob?.status === "running";
  const pageTitle = nav.find((n) => n[0] === page)![1];
  const selectedSourceLocationCount = data.materials
    .filter((material) =>
      selectedMaterialIds === null || selectedMaterialIds.includes(material.id),
    )
    .reduce(
      (sum, material) => sum + (material.chunking?.generation_location_count || 0),
      0,
    );
  return (
    <div className="app">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            go("home");
          }}
        >
          <span className="brand-icon">
            <GraduationCap size={24} />
          </span>
          <span>
            昇格ラボ<small>SHOKAKU LAB</small>
          </span>
        </a>
        <div className="workspace-label">MY WORKSPACE</div>
        <nav>
          {nav.map(([key, label, Icon]) => (
            <button
              key={key}
              className={page === key ? "nav-item selected" : "nav-item"}
              onClick={() => go(key)}
            >
              <Icon size={19} />
              {label}
              {key === "bank" && (
                <span className="nav-count">{active.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-tip">
          <span className="tiny">LEARN AT YOUR PACE</span>
          <p>
            毎日の一問が、
            <br />
            次の自分につながる。
          </p>
          <div className="mini-bars">
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
        </div>
        <div className="profile">
          <span className="avatar">私</span>
          <div>
            個人ワークスペース<small>ローカル環境</small>
          </div>
          <Settings2 size={17} />
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            ワークスペース <ChevronRight size={14} />{" "}
            <strong>{pageTitle}</strong>
          </div>
          <span className="save-status" role="status">
            {status.includes("オフライン") ? (
              <WifiOff size={15} />
            ) : (
              <Cloud size={15} />
            )}{" "}
            {status}
          </span>
        </header>
        <div className="content">
          {error && (
            <div className="error" role="alert">
              {error}
              <button aria-label="エラーを閉じる" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {generationJob && (
            <section className="panel generation-job" aria-live="polite">
              <div className="generation-job-copy">
                <span className={"import-status " + generationJob.status}>
                  {generationJob.status === "queued"
                    ? "待機中"
                    : generationJob.status === "running"
                      ? "生成中"
                      : generationJob.status === "complete"
                        ? "完了"
                        : "中断・失敗"}
                </span>
                <h3>
                  {generationJob.status === "complete"
                    ? "問題の生成が完了しました"
                    : generationJob.status === "failed"
                      ? "問題生成を完了できませんでした"
                      : "問題を生成しています"}
                </h3>
                <p>
                  {generationJob.completed} / {generationJob.total}{" "}
                  {generationJob.kind === "direct" ? "問" : "セット"}
                  {generationJob.error && <><br />{generationJob.error}</>}
                  {generationRunning && <><br />画面を移動しても生成は続きます。完了後にここから問題確認・演習へ進めます。</>}
                </p>
                {generationRunning && (
                  <div className="progress-track">
                    <i style={{ width: `${Math.round((generationJob.completed / Math.max(1, generationJob.total)) * 100)}%` }} />
                  </div>
                )}
              </div>
              {!!generationJob.set_ids.length && (
                <div className="generation-job-actions">
                  <button
                    className="button"
                    onClick={() => {
                      go("bank");
                      setFocusQuestionSetIds(generationJob.set_ids);
                      setSetFilterId(generationJob.set_ids.length === 1 ? generationJob.set_ids[0] : "job");
                    }}
                  >
                    問題を確認
                  </button>
                  {data.questions.some((question) => generationJob.set_ids.includes(question.question_set_id)) && (
                    <button
                      className="button primary"
                      onClick={() => start(
                        data.questions.filter((question) => generationJob.set_ids.includes(question.question_set_id)),
                        "生成した問題の演習",
                      )}
                    >
                      生成した問題を演習
                    </button>
                  )}
                </div>
              )}
            </section>
          )}
          {!ready ? (
            <div className="empty">学習データを読み込んでいます…</div>
          ) : (
            <>
              {page === "home" && (
                <>
                  <div className="page-heading">
                    <div>
                      <span className="eyebrow">YOUR LEARNING JOURNEY</span>
                      <h1>今日も、一歩先へ。</h1>
                      <p>知識を積み重ねて、次のステップに備えましょう。</p>
                    </div>
                    <span className="date-chip">
                      {new Date().toLocaleDateString("ja-JP", {
                        month: "long",
                        day: "numeric",
                        weekday: "short",
                      })}
                    </span>
                  </div>
                  <section className="hero">
                    <div>
                      <span className="hero-tag">
                        <span /> 自分のペースで、着実に
                      </span>
                      <h2>
                        {attempt
                          ? "前回の続きから、始めよう。"
                          : "まずは、一問から始めよう。"}
                      </h2>
                      <p>
                        {attempt
                          ? `${attempt.name} · ${attempt.index + 1} / ${attempt.questions.length} 問目`
                          : "サンプル問題で、答え合わせと復習を体験できます。"}
                        <br />
                        回答は自動保存。いつでもここから再開できます。
                      </p>
                      <button
                        className="button light"
                        onClick={() => (attempt ? go("practice") : start())}
                      >
                        {attempt ? "演習を再開する" : "演習を始める"}
                        <ArrowRight size={17} />
                      </button>
                    </div>
                    <div className="hero-art" aria-hidden="true">
                      <div className="orbit" />
                      <div className="paper back" />
                      <div className="paper front">
                        <div className="paper-title" />
                        <div className="paper-line" />
                        <div className="paper-line short" />
                        <div className="paper-choice">
                          <Check size={13} /> <i />
                        </div>
                        <div className="paper-choice">
                          <span /> <i />
                        </div>
                        <div className="paper-choice">
                          <span /> <i />
                        </div>
                      </div>
                      <span className="floating-star">✦</span>
                      <span className="floating-check">
                        <Check />
                      </span>
                    </div>
                  </section>
                  <div className="stats-grid">
                    <Stat
                      title="解いた問題"
                      value={results.length}
                      suffix="問"
                      icon={<BookOpen size={20} />}
                      detail="これまでの学習の積み重ね"
                    />
                    <Stat
                      title="正答率"
                      value={
                        scored.length
                          ? Math.round((correct / scored.length) * 100)
                          : "—"
                      }
                      suffix="%"
                      icon={<ChartNoAxesCombined size={20} />}
                      detail="採点済みの回答から集計"
                    />
                    <Stat
                      title="復習したい問題"
                      value={wrongIds.size}
                      suffix="問"
                      icon={<RotateCcw size={20} />}
                      detail="もう一度解いて、理解を深める"
                    />
                  </div>
                  <div className="dashboard-grid">
                    <section className="panel">
                      <div className="section-heading">
                        <h3>学習を進める</h3>
                        <span className="muted">
                          小さな積み重ねを、毎日に。
                        </span>
                      </div>
                      <button
                        className="action-row"
                        onClick={() =>
                          start(
                            [...active].sort(() => Math.random() - 0.5),
                            "ランダム演習",
                          )
                        }
                      >
                        <span className="action-icon mint">
                          <BookOpen />
                        </span>
                        <div>
                          <strong>ランダムに演習</strong>
                          <p>さまざまなカテゴリから、知識をチェック</p>
                        </div>
                        <ArrowRight size={19} />
                      </button>
                      <button
                        className="action-row"
                        onClick={() =>
                          start(
                            active.filter((q) => wrongIds.has(q.id)),
                            "間違えた問題の復習",
                          )
                        }
                      >
                        <span className="action-icon peach">
                          <RotateCcw />
                        </span>
                        <div>
                          <strong>間違えた問題を復習</strong>
                          <p>苦手なポイントを、確かな知識に</p>
                        </div>
                        <ArrowRight size={19} />
                      </button>
                      <button
                        className="action-row"
                        onClick={() => go("materials")}
                      >
                        <span className="action-icon lavender">
                          <Sparkles />
                        </span>
                        <div>
                          <strong>資料から問題をつくる</strong>
                          <p>資料を追加し、形式を選んで問題を作る</p>
                        </div>
                        <ArrowRight size={19} />
                      </button>
                    </section>
                    <section className="panel category-panel">
                      <div className="section-heading">
                        <h3>カテゴリ別の理解度</h3>
                        <button
                          className="text-button"
                          onClick={() => go("analytics")}
                        >
                          詳しく <ArrowRight size={14} />
                        </button>
                      </div>
                      {categories.slice(0, 4).map((c) => {
                        const r = scored.filter((x) => x.q.category === c),
                          v = r.length
                            ? Math.round(
                                (r.filter((x) => x.r.correct).length /
                                  r.length) *
                                  100,
                              )
                            : 0;
                        return (
                          <div className="category-progress" key={c}>
                            <div>
                              <span>{c}</span>
                              <strong>{r.length ? v + "%" : "未学習"}</strong>
                            </div>
                            <div className="progress-track">
                              <i style={{ width: v + "%" }} />
                            </div>
                          </div>
                        );
                      })}
                      <p className="footnote">
                        演習するたびに、理解度が更新されます。
                      </p>
                    </section>
                  </div>
                  <section className="setup-strip">
                    <span className="action-icon">
                      <Files />
                    </span>
                    <div>
                      <strong>あなた専用の問題集をつくりませんか？</strong>
                      <p>試験範囲の資料を登録して、学習をカスタマイズ。</p>
                    </div>
                    <button className="button" onClick={() => go("materials")}>
                      資料を登録 <Plus size={16} />
                    </button>
                  </section>
                </>
              )}
              {page === "practice" && (
                <>
                  <Heading
                    eyebrow="PRACTICE"
                    title="一問ずつ、理解を深める。"
                    description="時間制限はありません。納得するまで、じっくりと。"
                  />
                  {!q || !attempt ? (
                    <div className="panel empty">
                      <BookOpen size={36} />
                      <h3>演習を始めましょう</h3>
                      <p>登録された問題を使って学習できます。</p>
                      <button
                        className="button primary"
                        onClick={() => start()}
                      >
                        全問題で演習する
                      </button>
                      <button className="button" onClick={() => go("bank")}>
                        問題を選ぶ
                      </button>
                    </div>
                  ) : (
                    <div className="practice-grid">
                      <section className="panel question-panel">
                        <div className="question-meta">
                          <span className="tag">{q.category}</span>
                          <span>
                            {typeNames[q.question_type]} · {q.parent}
                          </span>
                          <span className="question-counter">
                            {attempt.index + 1} / {attempt.questions.length}
                          </span>
                        </div>
                        <h2 className="question-body">{q.body}</h2>
                        {q.warnings.map((w, i) => (
                          <div className="warning" key={i}>
                            {w}
                          </div>
                        ))}
                        <div className="choices">
                          {q.question_type === "choice" ? (
                            q.choices.map((choice, i) => (
                              <button
                                key={i}
                                disabled={!!result}
                                className={
                                  "choice " +
                                  (attempt.answers[q.id] === choice
                                    ? "chosen "
                                    : "") +
                                  (result && choice === q.answer
                                    ? "correct-choice"
                                    : "")
                                }
                                onClick={() =>
                                  patch({
                                    answers: {
                                      ...attempt.answers,
                                      [q.id]: choice,
                                    },
                                  })
                                }
                              >
                                <span>{String.fromCharCode(65 + i)}</span>
                                {choice}
                                {attempt.answers[q.id] === choice && (
                                  <Check size={18} />
                                )}
                              </button>
                            ))
                          ) : (
                            <label className="field">
                              あなたの解答
                              <textarea
                                aria-label="あなたの解答"
                                rows={q.question_type === "short" ? 5 : 2}
                                disabled={!!result}
                                value={attempt.answers[q.id] || ""}
                                onChange={(e) =>
                                  patch({
                                    answers: {
                                      ...attempt.answers,
                                      [q.id]: e.target.value,
                                    },
                                  })
                                }
                                placeholder={
                                  q.question_type === "blank"
                                    ? "複数の空欄は / で順番に区切って入力"
                                    : "解答を入力してください"
                                }
                              />
                            </label>
                          )}
                        </div>
                        {!result ? (
                          <button
                            className="button primary grade-button"
                            disabled={busy || !attempt.answers[q.id]?.trim()}
                            onClick={() => perform(grade)}
                          >
                            <Check size={17} />
                            {busy ? "採点中…" : "答え合わせ"}
                          </button>
                        ) : (
                          <div
                            className={
                              "result " +
                              (result.correct === false ? "incorrect" : "")
                            }
                          >
                            <h3>
                              {result.correct === null
                                ? "参考解答"
                                : result.correct
                                  ? "正解です！"
                                  : "あと一歩。解説を確認しましょう。"}
                            </h3>
                            <p>
                              {result.reason}
                              {result.reference &&
                                result.score !== null &&
                                `（参考点 ${Math.round(result.score * q.score_weight * 100) / 100} / ${q.score_weight}）`}
                            </p>
                            <strong>正解：{q.answer}</strong>
                            <p className="pre-wrap">{q.explanation}</p>
                            {q.source_references.map((s, i) => (
                              <details key={i}>
                                <summary>
                                  根拠：{s.material_name}{" "}
                                  {s.page_number ? `p.${s.page_number}` : ""}{" "}
                                  {s.line_start ? `${sourceLineLabel(s.file_type, s.line_basis, s.ocr_confidence)} ${s.line_start}${s.line_end && s.line_end !== s.line_start ? `–${s.line_end}` : ""}` : ""}{" "}
                                  {s.char_start ? `文字 ${s.char_start}–${s.char_end}` : ""}{" "}
                                  {s.slide_number ? `スライド ${s.slide_number}` : ""}{" "}
                                  {s.sheet_name} {s.cell_range}
                                </summary>
                                <p className="pre-wrap">{s.text}</p>
                                {data.materials.some(
                                  (material) => material.id === s.material_id,
                                ) ? (
                                  <a
                                    href={`/api/documents/materials/${s.material_id}/file`}
                                  >
                                    元の資料をダウンロード
                                  </a>
                                ) : s.source_url ? (
                                  <a href={s.source_url} target="_blank" rel="noreferrer">
                                    登録元URLを開く
                                  </a>
                                ) : (
                                  <span className="muted">元資料は削除済みです</span>
                                )}
                              </details>
                            ))}
                          </div>
                        )}
                        <div className="question-tools">
                          <button
                            className="text-button"
                            onClick={() => {
                              const bankQuestion = data.questions.find((item) => item.id === q.id);
                              setEditor(structuredClone(bankQuestion || q));
                            }}
                          >
                            問題を確認・編集
                          </button>
                          <button
                            className={
                              "text-button " +
                              (attempt.flags[q.id] ? "marked" : "")
                            }
                            onClick={() =>
                              patch({
                                flags: {
                                  ...attempt.flags,
                                  [q.id]: !attempt.flags[q.id],
                                },
                              })
                            }
                          >
                            <Flag size={16} />
                            {attempt.flags[q.id] ? "見直し中" : "見直しに追加"}
                          </button>
                          <button
                            className="text-button"
                            onClick={() =>
                              perform(async () => {
                                const live = data.questions.find(
                                  (x) => x.id === q.id,
                                );
                                if (live)
                                  await saveQ({
                                    ...live,
                                    favorite: !live.favorite,
                                  });
                              })
                            }
                          >
                            <Star
                              size={16}
                              fill={
                                data.questions.find((x) => x.id === q.id)
                                  ?.favorite
                                  ? "currentColor"
                                  : "none"
                              }
                            />
                            お気に入り
                          </button>
                        </div>
                        <label className="field">
                          自分用メモ
                          <textarea
                            rows={2}
                            value={attempt.notes[q.id] || ""}
                            onChange={(e) =>
                              patch({
                                notes: {
                                  ...attempt.notes,
                                  [q.id]: e.target.value,
                                },
                              })
                            }
                            placeholder="気づいたことや、覚えておきたいこと"
                          />
                        </label>
                        <div className="question-navigation">
                          <button
                            className="button"
                            disabled={attempt.index === 0}
                            onClick={() => navigate(-1)}
                          >
                            <ArrowLeft size={16} />
                            前の問題
                          </button>
                          {attempt.index < attempt.questions.length - 1 ? (
                            <button
                              className="button primary"
                              onClick={() => navigate(1)}
                            >
                              次の問題
                              <ArrowRight size={16} />
                            </button>
                          ) : (
                            <button
                              className="button primary"
                              onClick={() => go("analytics")}
                            >
                              学習の記録を見る
                              <ChartNoAxesCombined size={16} />
                            </button>
                          )}
                        </div>
                      </section>
                      <aside className="panel practice-aside">
                        <h3>今回の演習</h3>
                        <p>{attempt.name}</p>
                        <div className="progress-track">
                          <i
                            style={{
                              width:
                                (Object.keys(attempt.results).length /
                                  attempt.questions.length) *
                                  100 +
                                "%",
                            }}
                          />
                        </div>
                        <p className="muted">
                          {Object.keys(attempt.results).length} /{" "}
                          {attempt.questions.length} 問 回答済み
                        </p>
                        <div className="number-grid">
                          {attempt.questions.map((x, i) => (
                            <button
                              key={x.id}
                              aria-label={`問題 ${i + 1}`}
                              className={
                                (i === attempt.index ? "current " : "") +
                                (attempt.results[x.id] ? "answered " : "") +
                                (attempt.flags[x.id] ? "flagged" : "")
                              }
                              onClick={() => patch({ index: i })}
                            >
                              {i + 1}
                            </button>
                          ))}
                        </div>
                        <div className="legend">
                          ■ 回答済み　 <span>●</span> 見直し
                        </div>
                        <p className="footnote">
                          ← → キーで問題を移動できます。
                          <br />
                          解答とメモは端末に自動保存されます。
                        </p>
                        <button className="button" onClick={() => go("bank")}>
                          別の問題を選ぶ
                        </button>
                      </aside>
                    </div>
                  )}
                </>
              )}
              {page === "bank" && (
                <>
                  <Heading
                    eyebrow="QUESTION BANK"
                    title="知識を育てる、問題バンク。"
                    description="問題を整理して、今の自分に必要な演習を。"
                    action={
                      <button
                        className="button primary"
                        onClick={() => setEditor(freshQ())}
                      >
                        <Plus size={17} />
                        問題を追加
                      </button>
                    }
                  />
                  <div className="panel">
                    <div className="toolbar">
                      <label className="search">
                        <Search size={18} />
                        <input
                          aria-label="問題を検索"
                          placeholder="問題文・カテゴリで検索"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                        />
                      </label>
                      <select
                        aria-label="カテゴリ絞り込み"
                        value={cat}
                        onChange={(e) => setCat(e.target.value)}
                      >
                        <option>すべて</option>
                        {categories.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                      <select
                        aria-label="形式絞り込み"
                        value={typ}
                        onChange={(e) => setTyp(e.target.value)}
                      >
                        <option value="すべて">すべての形式</option>
                        {Object.entries(typeNames).map(([k, v]) => (
                          <option key={k} value={k}>
                            {v}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="toolbar secondary-toolbar">
                      <select
                        aria-label="問題セット"
                        value={setFilterId}
                        onChange={(e) => {
                          if (e.target.value === "job") return;
                          setFocusQuestionSetIds(null);
                          setSetFilterId(e.target.value);
                          setMajorFilter("すべて");
                        }}
                      >
                        <option value="すべて">すべてのセット</option>
                        {setFilterId === "job" && focusQuestionSetIds && (
                          <option value="job">今回生成した問題</option>
                        )}
                        <option value="sample">サンプル問題</option>
                        {data.sets.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name}
                          </option>
                        ))}
                      </select>
                      <select
                        aria-label="大問"
                        value={majorFilter}
                        onChange={(e) => setMajorFilter(e.target.value)}
                      >
                        <option value="すべて">すべての大問</option>
                        {[
                          ...new Set(
                            data.questions
                              .filter(questionMatchesSetFilter)
                              .map((q) => q.parent),
                          ),
                        ].map((p) => (
                          <option key={p}>{p}</option>
                        ))}
                      </select>
                    </div>
                    <div className="tabs">
                      {[
                        ["active", "すべて"],
                        ["favorite", "お気に入り"],
                        ["wrong", "間違えた問題"],
                      ].map(([k, v]) => (
                        <button
                          className={filter === k ? "active" : ""}
                          key={k}
                          onClick={() => setFilter(k)}
                        >
                          {v}
                        </button>
                      ))}
                    </div>
                    <div className="list-caption">
                      <span>{filtered.length} 問の問題</span>
                      <button
                        className="text-button"
                        onClick={() => start(filtered, "選択した条件で演習")}
                      >
                        この条件で演習 <Play size={14} />
                      </button>
                    </div>
                    {!filtered.length ? (
                      <div className="empty">条件に合う問題がありません。</div>
                    ) : (
                      filtered.map((x, i) => (
                        <div className="bank-row" key={x.id}>
                          <span className="row-number">
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <button
                            className="bank-body"
                            onClick={() => setEditor(structuredClone(x))}
                          >
                            <div>
                              <span className="tag">{x.category}</span>
                              <span className="muted">
                                {typeNames[x.question_type]}
                              </span>
                              {x.warnings.length > 0 && (
                                <span className="warning-dot">要確認</span>
                              )}
                            </div>
                            <strong>{x.body}</strong>
                          </button>
                          <button
                            className="icon-button"
                            aria-label="この問題を演習"
                            title="この問題を演習"
                            onClick={() => start([structuredClone(x)], "問題バンクからの演習")}
                          >
                            <Play size={17} />
                          </button>
                          <button
                            className="icon-button"
                            aria-label="お気に入り切り替え"
                            onClick={() =>
                              perform(() =>
                                saveQ({ ...x, favorite: !x.favorite }),
                              )
                            }
                          >
                            <Star
                              size={18}
                              fill={x.favorite ? "currentColor" : "none"}
                            />
                          </button>
                          <button
                            className="icon-button"
                            aria-label="問題を完全削除"
                            title="問題を完全削除"
                            onClick={() => {
                              if (
                                window.confirm(
                                  "この問題を完全に削除しますか？問題と、この問題を含む演習履歴・編集履歴が削除され、元に戻せません。",
                                )
                              )
                                void perform(() => deleteQuestion(x.id));
                            }}
                          >
                            <Trash2 size={18} />
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </>
              )}
              {page === "materials" && (
                <>
                  <Heading
                    eyebrow="STUDY MATERIALS"
                    title="今年の資料を、学びの土台に。"
                    description="問題の内容・正解・解説の根拠となる資料を登録します。"
                  />
                  <div className="workflow-step">1. 資料を追加</div>
                  <label
                    className={"upload-zone " + (busy ? "disabled" : "")}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      if (!busy && e.dataTransfer.files.length)
                        perform(() => runImports(fileItems(e.dataTransfer.files)));
                    }}
                  >
                    <span className="upload-icon">
                      <Upload size={26} />
                    </span>
                    <h3>
                      {busy ? "資料を読み込み中…" : "ファイルを選ぶ・ここにドロップ"}
                    </h3>
                    <p>PDF・Excel・PowerPoint・画像・テキスト / 複数可・各100MBまで。長い資料は自動で分割します。</p>
                    <span className="button">
                      ファイルを選択
                      <Plus size={16} />
                    </span>
                    <input
                      aria-label="資料ファイル"
                      type="file"
                      multiple
                      disabled={busy}
                      accept=".pdf,.xlsx,.xls,.pptx,.ppt,.html,.htm,.png,.jpg,.jpeg,.webp,.txt"
                      onChange={(e) => {
                        const items = e.target.files ? fileItems(e.target.files) : [];
                        if (items.length)
                          perform(() => runImports(items));
                        e.target.value = "";
                      }}
                    />
                  </label>
                  <form className="url-import" onSubmit={(e) => {
                    e.preventDefault();
                    const value = urlInput.trim();
                    if (!value) return;
                    setUrlInput("");
                    perform(() => runImports([{
                      id: crypto.randomUUID(), name: value, url: value, status: "waiting",
                    }]));
                  }}>
                    <input
                      aria-label="WebページのURL"
                      type="url"
                      required
                      placeholder="https://... WebページやPDFのURL"
                      value={urlInput}
                      disabled={busy}
                      onChange={(e) => setUrlInput(e.target.value)}
                    />
                    <button className="button" disabled={busy}>URLを追加</button>
                  </form>
                  <details className="import-options">
                    <summary>PDFの読み取り方法を変更</summary>
                    <Field label="読み取り方法">
                      <select
                        value={analysisMethod}
                        onChange={(e) => setAnalysisMethod(e.target.value as AnalysisMethod)}
                        disabled={busy}
                      >
                        <option value="standard">自動（テキスト抽出とOCR）</option>
                        <option value="multimodal">OpenAIで画像を解析</option>
                      </select>
                      <span className="muted upload-method-help">文字化けしたPDF向け。OpenAI接続とAPI利用料が必要です。</span>
                    </Field>
                  </details>
                  {!!imports.length && <div className="import-list" aria-live="polite">
                    {imports.map((item) => <div className="import-item" key={item.id}>
                      <span className="import-name">{item.name}</span>
                      <span className={"import-status " + item.status}>
                        {item.status === "waiting" ? "待機中" : item.status === "reading" ? "読み込み中" : item.status === "done" ? "読み込み済み" : "失敗"}
                      </span>
                      {item.status === "error" && <>
                        <span className="import-error">{item.error}</span>
                        <button className="text-button" disabled={busy} onClick={() => perform(() => runImports([{ ...item, status: "waiting" }]))}>再試行</button>
                      </>}
                    </div>)}
                  </div>}
                  <div className="section-heading document-heading">
                    <h3>登録した資料</h3>
                    <span className="muted">{data.materials.length} 件</span>
                  </div>
                  <div className="document-grid">
                    {data.materials.map((d) => (
                      <button
                        key={d.id}
                        className="panel document-card"
                        onClick={() => {
                          perform(async () => {
                            setDoc(await api(`/documents/materials/${d.id}`));
                          });
                        }}
                      >
                        <span className="document-icon">
                          <Files size={23} />
                        </span>
                        <span className="tag">
                          {d.status === "review" ? "確認待ち" : "確認済み"}
                        </span>
                        <h3>{d.name}</h3>
                        <p>
                          {d.chunks.length} チャンク · バージョン {d.version}
                          {d.chunking?.auto_split && " · 長文を自動分割済み"}
                        </p>
                        <div>
                          {d.categories.map((c) => (
                            <span className="tag" key={c}>
                              {c}
                            </span>
                          ))}
                        </div>
                        <span className="text-button">
                          解析結果を確認・編集 <ArrowRight size={15} />
                        </span>
                      </button>
                    ))}
                  </div>
                  {!data.materials.length && (
                    <div className="empty subtle">
                      まだ資料はありません。最初のファイルを登録しましょう。
                    </div>
                  )}
                  <section className="panel quick-generate">
                      <div className="workflow-step">2. 形式を選んで問題を作成</div>
                      <h3>読み込んだ資料から問題を作る</h3>
                      <p>登録済みの資料から新しい問題を作ります。再アップロードせず、使う資料・形式・問数・難易度を指定できます。</p>
                      {!!data.materials.length && <fieldset className="source-picker">
                        <legend>使う資料</legend>
                        {data.materials.map((material) => {
                          const chosen = selectedMaterialIds === null || selectedMaterialIds.includes(material.id);
                          return <label className="source-option" key={material.id}>
                            <input type="checkbox" checked={chosen} disabled={busy}
                              onChange={(e) => {
                                const current = selectedMaterialIds ?? data.materials.map((item) => item.id);
                                setSelectedMaterialIds(e.target.checked
                                  ? [...current, material.id]
                                  : current.filter((id) => id !== material.id));
                              }} />
                            <span><strong>{material.name}</strong><small>{material.chunks.length} 範囲を読み込み済み</small></span>
                          </label>;
                        })}
                      </fieldset>}
                      <div className="quick-controls">
                        <Field label="問題形式">
                          <select value={quickType} disabled={busy} onChange={(e) => setQuickType(e.target.value as Question["question_type"])}>
                            {Object.entries(typeNames).map(([key, name]) => <option key={key} value={key}>{name}</option>)}
                          </select>
                        </Field>
                        <Field label="問数">
                          <input type="number" min={1} max={1000} value={quickCount} disabled={busy} onChange={(e) => setQuickCount(Number(e.target.value))} />
                        </Field>
                        <Field label="難易度">
                          <select value={quickDifficulty} disabled={busy} onChange={(e) => setQuickDifficulty(e.target.value)}>
                            {["基礎", "標準", "応用"].map((level) => <option key={level}>{level}</option>)}
                          </select>
                        </Field>
                      </div>
                      {selectedSourceLocationCount > 0 && <p className="quick-mode-note">
                        読み込んだ資料には約{selectedSourceLocationCount.toLocaleString()}個のページ・行範囲があります。未使用範囲を優先し、足りない場合は既使用範囲も別の問題に再利用します。既存問題はNGリストとして生成時に渡します。
                      </p>}
                      <p className="quick-mode-note">最大1,000問をバックグラウンドで作成します。画面を移動したり再読み込みしたりしても生成は続き、完了後に問題確認・演習へ移れます。問数に応じてAI利用料が増えます。</p>
                      {data.provider === "mock" && <p className="quick-mode-note">現在は資料の抜粋を使う簡易生成です。OpenAI接続時は指定形式・難易度に沿った問題を生成します。</p>}
                      <button className="button primary" disabled={busy || generationRunning || !data.materials.length} onClick={() => perform(quickGenerate)}>
                        <Sparkles size={16} /> {generationRunning ? "生成中…" : busy ? "処理中…" : "問題を作成"}
                      </button>
                  </section>
                </>
              )}
              {page === "recipes" && (
                <>
                  <Heading
                    eyebrow="GENERATION RECIPES"
                    title="あなたの試験に合う、出題を。"
                    description="今年の資料のカテゴリと問題形式を指定します。"
                    action={
                      <button
                        className="button primary"
                        onClick={() => setRecipe(freshRecipe())}
                      >
                        <Plus size={17} />
                        レシピを作成
                      </button>
                    }
                  />
                  <div className="notice">
                    <Sparkles size={20} />
                    <div>
                      <strong>
                        {data.provider === "mock"
                          ? "デモ生成モード"
                          : "OpenAI 生成モード"}
                      </strong>
                      <p>
                        {data.provider === "mock"
                          ? "APIキーなしで操作を試せます。デモ問題は資料の抜粋です。本格的な生成はサーバーのOpenAI接続設定で有効にできます。"
                          : "生成時は選択した資料と形式情報をOpenAI APIへ送信します。生成結果の根拠と品質警告を確認してください。"}
                      </p>
                    </div>
                  </div>
                  <div className="document-grid">
                    {data.recipes.map((r) => (
                      <section className="panel recipe-card" key={r.id}>
                        <span className="action-icon mint">
                          <SlidersHorizontal />
                        </span>
                        <h3>{r.name}</h3>
                        <div>
                          <span className="tag">{r.category}</span>
                          <span className="tag neutral">
                            {typeNames[r.question_type]}
                          </span>
                        </div>
                        <p>
                          {r.major_count} 大問 × {r.sub_count} 小問 ·{" "}
                          {r.difficulty}
                          <br />
                          問題文 約{r.body_length}文字
                          <br />
                          使用資料：
                          {(r.material_ids || []).length
                            ? (r.material_ids || [])
                                .map(
                                  (id) =>
                                    data.materials.find((m) => m.id === id)
                                      ?.name || "削除済みの資料",
                                )
                                .join("、")
                            : `「${r.category}」に一致する全資料`}
                        </p>
                        <div className="recipe-actions">
                          <button
                            className="button"
                            onClick={() => setRecipe({ ...r })}
                          >
                            編集
                          </button>
                          <button
                            className="icon-button"
                            aria-label="レシピを複製"
                            onClick={() =>
                              setRecipe({
                                ...r,
                                id: "",
                                name: r.name + " のコピー",
                              })
                            }
                          >
                            <Copy size={17} />
                          </button>
                          <button
                            className="button primary"
                            disabled={busy || generationRunning}
                            onClick={() =>
                              perform(async () => {
                                const job: GenerationJob = await api("/generation-jobs/recipe", {
                                  method: "POST",
                                  body: JSON.stringify({
                                    recipe_id: r.id,
                                    count: batch,
                                    material_ids: r.material_ids || [],
                                  }),
                                });
                                setGenerationJob(job);
                              })
                            }
                          >
                            <Sparkles size={16} />
                            {generationRunning ? "生成中…" : busy ? "受付中…" : "生成する"}
                          </button>
                        </div>
                      </section>
                    ))}
                  </div>
                  <label className="field batch-field">
                    一度に生成するセット数
                    <select
                      value={batch}
                      onChange={(e) => setBatch(Number(e.target.value))}
                    >
                      {[1, 2, 3, 4, 5].map((n) => (
                        <option key={n}>{n}</option>
                      ))}
                    </select>
                  </label>
                </>
              )}
              {page === "analytics" && (
                <>
                  <Heading
                    eyebrow="LEARNING INSIGHTS"
                    title="積み重ねが、見えてくる。"
                    description="得点は学習用の参考値です。未採点の短文記述は正答率に含みません。"
                  />
                  <div className="stats-grid">
                    <Stat
                      title="回答数"
                      value={results.length}
                      suffix="回"
                      detail="すべての演習セッション"
                      icon={<BookOpen />}
                    />
                    <Stat
                      title="正答率"
                      value={
                        scored.length
                          ? Math.round((correct / scored.length) * 100)
                          : "—"
                      }
                      suffix="%"
                      detail="採点済みの回答"
                      icon={<ChartNoAxesCombined />}
                    />
                    <Stat
                      title="復習対象"
                      value={wrongIds.size}
                      suffix="問"
                      detail="各問題の直近の判定から集計"
                      icon={<RotateCcw />}
                    />
                  </div>
                  <div className="dashboard-grid">
                    {[
                      ["カテゴリ別", categories],
                      ["問題形式別", Object.keys(typeNames)],
                    ].map(([title, groups]) => (
                      <section className="panel" key={title as string}>
                        <h3>{title}</h3>
                        {(groups as string[]).map((c) => {
                          const r = scored.filter((x) =>
                            title === "カテゴリ別"
                              ? x.q.category === c
                              : x.q.question_type === c,
                          );
                          const v = r.length
                            ? Math.round(
                                (r.filter((x) => x.r.correct).length /
                                  r.length) *
                                  100,
                              )
                            : 0;
                          return (
                            <div className="category-progress" key={c}>
                              <div>
                                <span>
                                  {typeNames[c as keyof typeof typeNames] || c}
                                </span>
                                <strong>
                                  {r.length ? v + "%" : "未学習"}{" "}
                                  <small>({r.length} 回)</small>
                                </strong>
                              </div>
                              <div className="progress-track">
                                <i style={{ width: v + "%" }} />
                              </div>
                            </div>
                          );
                        })}
                      </section>
                    ))}
                  </div>
                  <section className="panel history">
                    <div className="section-heading">
                      <h3>演習の履歴</h3>
                      <button
                        className="text-button"
                        onClick={() =>
                          start(
                            active.filter((q) => wrongIds.has(q.id)),
                            "間違えた問題の復習",
                          )
                        }
                      >
                        間違えた問題を復習 <ArrowRight size={15} />
                      </button>
                    </div>
                    {history.length ? (
                      history
                        .slice()
                        .reverse()
                        .map((a) => (
                          <button
                            className="history-row"
                            key={a.id}
                            onClick={() => {
                              persist(a);
                              go("practice");
                            }}
                          >
                            <BookOpen size={19} />
                            <div>
                              <strong>{a.name}</strong>
                              <p>
                                {new Date(a.updated_at).toLocaleString("ja-JP")}
                              </p>
                            </div>
                            <span>
                              {Object.keys(a.results).length} /{" "}
                              {a.questions.length} 問
                            </span>
                            <ChevronRight size={18} />
                          </button>
                        ))
                    ) : (
                      <div className="empty">
                        演習を始めると、ここに記録がたまります。
                      </div>
                    )}
                  </section>
                </>
              )}
            </>
          )}
          <footer>
            昇格ラボ <span>ひとつずつ、自信に変えていこう。</span>
            <span className="footer-mode">
              {data.provider === "mock" ? "デモ / AI未接続" : "OpenAI接続"} ·
              個人利用
            </span>
          </footer>
        </div>
      </main>
      {editor && (
        <Modal
          title={editor.id ? "問題を編集" : "問題を追加"}
          onClose={() => setEditor(null)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              perform(() => saveQ(editor));
            }}
          >
            <div className="form-grid">
              <Field label="カテゴリ">
                <input
                  required
                  value={editor.category}
                  onChange={(e) =>
                    setEditor({ ...editor, category: e.target.value })
                  }
                />
              </Field>
              <Field label="問題形式">
                <select
                  value={editor.question_type}
                  onChange={(e) =>
                    setEditor({
                      ...editor,
                      question_type: e.target
                        .value as Question["question_type"],
                    })
                  }
                >
                  {Object.entries(typeNames).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Field label="問題文">
              <textarea
                required
                rows={5}
                value={editor.body}
                onChange={(e) => setEditor({ ...editor, body: e.target.value })}
              />
            </Field>
            {editor.question_type === "choice" && (
              <Field label="選択肢（1行に1つ）">
                <textarea
                  required
                  rows={4}
                  value={editor.choices.join("\n")}
                  onChange={(e) =>
                    setEditor({
                      ...editor,
                      choices: e.target.value.split("\n"),
                    })
                  }
                />
              </Field>
            )}
            <Field label="正解（選択問題は選択肢と同じ文を入力）">
              <textarea
                required
                rows={2}
                value={editor.answer}
                onChange={(e) =>
                  setEditor({ ...editor, answer: e.target.value })
                }
              />
            </Field>
            <Field label="許容解答（1行に1つ）">
              <textarea
                rows={2}
                value={editor.accepted_answers.join("\n")}
                onChange={(e) =>
                  setEditor({
                    ...editor,
                    accepted_answers: e.target.value
                      ? e.target.value.split("\n")
                      : [],
                  })
                }
              />
            </Field>
            <Field label="解説">
              <textarea
                rows={4}
                value={editor.explanation}
                onChange={(e) =>
                  setEditor({ ...editor, explanation: e.target.value })
                }
              />
            </Field>
            <Field label="採点基準（短文記述）">
              <textarea
                rows={2}
                value={editor.grading_rubric}
                onChange={(e) =>
                  setEditor({ ...editor, grading_rubric: e.target.value })
                }
              />
            </Field>
            <Field label="問題メモ">
              <textarea
                rows={2}
                value={editor.note}
                onChange={(e) => setEditor({ ...editor, note: e.target.value })}
              />
            </Field>
            {editor.warnings.map((w, i) => (
              <div className="warning" key={i}>
                {w}
              </div>
            ))}
            {editor.source_references.map((s, i) => (
              <details key={i}>
                <summary>
                  {s.material_name} {s.page_number ? `p.${s.page_number}` : ""} {s.line_start ? `${sourceLineLabel(s.file_type, s.line_basis, s.ocr_confidence)} ${s.line_start}${s.line_end && s.line_end !== s.line_start ? `–${s.line_end}` : ""}` : ""} {s.char_start ? `文字 ${s.char_start}–${s.char_end}` : ""} {s.slide_number ? `スライド ${s.slide_number}` : ""} {s.sheet_name}{" "}
                  {s.cell_range}
                </summary>
                <p>{s.text}</p>
                {data.materials.some(
                  (material) => material.id === s.material_id,
                ) ? (
                  <a href={`/api/documents/materials/${s.material_id}/file`}>
                    根拠資料をダウンロード
                  </a>
                ) : s.source_url ? (
                  <a href={s.source_url} target="_blank" rel="noreferrer">
                    登録元URLを開く
                  </a>
                ) : (
                  <span className="muted">元資料は削除済みです</span>
                )}
              </details>
            ))}
            <div className="modal-actions">
              {editor.id && (
                <button
                  type="button"
                  className="button"
                  disabled={busy || generationRunning}
                  onClick={() => perform(async () => {
                    await saveQ(editor);
                    start([editor], "問題確認からの演習");
                  })}
                >
                  この問題を演習
                </button>
              )}
              {editor.recipe_id && (
                <button
                  type="button"
                  disabled={busy || generationRunning}
                  className="button"
                  onClick={() =>
                    perform(async () => {
                      const job: GenerationJob = await api(`/generation-jobs/questions/${editor.id}/regenerate`, {
                        method: "POST",
                      });
                      setGenerationJob(job);
                      setEditor(null);
                    })
                  }
                >
                  別の資料範囲から再生成
                </button>
              )}
              <button className="button primary" disabled={busy}>
                保存する
              </button>
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </form>
        </Modal>
      )}
      {doc && (
        <Modal title="解析結果を確認・編集" onClose={() => setDoc(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              perform(async () => {
                await api(
                  `/documents/materials/${doc.id}`,
                  {
                    method: "PUT",
                    body: JSON.stringify({ ...doc, status: "confirmed" }),
                  },
                );
                setDoc(null);
                await refresh();
              });
            }}
          >
            <Field label="資料名">
              <input
                required
                value={doc.name}
                onChange={(e) => setDoc({ ...doc, name: e.target.value })}
              />
            </Field>
            <Field label="カテゴリ（カンマ区切り・複数指定可）">
              <input
                value={doc.categories.join(",")}
                onChange={(e) => {
                  const cats = e.target.value.split(",").map((s) => s.trim());
                  setDoc({
                    ...doc,
                    categories: cats,
                    chunks: doc.chunks.map((c) => ({
                      ...c,
                      categories: cats,
                    })),
                  });
                }}
              />
            </Field>
            {doc.warnings.map((w, i) => (
              <div className="warning" key={i}>
                {w}
              </div>
            ))}
            <p className="muted">
              解析方式：{documentAnalysisLabel(doc)}{" "}
              · 抽出結果は編集して確定できます。
              {doc.source_url && <><br />元のURL：<a href={doc.source_url} target="_blank" rel="noreferrer">{doc.source_url}</a></>}
              {doc.chunking && (
                <>
                  <br />
                  最大{doc.chunking.max_chars.toLocaleString()}文字単位 ·{" "}
                  {doc.chunking.analysis_batch_count}処理バッチ
                  {doc.chunking.auto_split &&
                    ` · 元の${doc.chunking.source_chunk_count}範囲を${doc.chunking.chunk_count}チャンクへ自動分割`}
                </>
              )}
            </p>
            {doc.chunks.map((c, i) => (
              <section className="chunk" key={c.id}>
                <h4>
                  {c.page_number
                    ? `ページ ${c.page_number}`
                    : `チャンク ${i + 1}`}{" "}
                  {c.line_start ? `${sourceLineLabel(doc.file_type, c.line_basis, c.ocr_confidence)} ${c.line_start}${c.line_end && c.line_end !== c.line_start ? `–${c.line_end}` : ""}` : ""}{" "}
                  {c.char_start ? `文字 ${c.char_start}–${c.char_end}` : ""}{" "}
                  {c.slide_number ? `スライド ${c.slide_number}` : ""}{" "}
                  {c.sheet_name} {c.cell_range}{" "}
                  {c.ocr_confidence !== undefined &&
                    `OCR信頼度 ${c.ocr_confidence}%`}
                </h4>
                <textarea
                  aria-label={`本文 ${i + 1}`}
                  rows={5}
                  value={c.text}
                  onChange={(e) =>
                    setDoc({
                      ...doc,
                      chunks: doc.chunks.map((x, j) =>
                        j === i ? { ...x, text: e.target.value } : x,
                      ),
                    })
                  }
                />
                <Field label="この範囲のカテゴリ">
                  <input
                    value={(c.categories || []).join(",")}
                    onChange={(e) =>
                      setDoc({
                        ...doc,
                        chunks: doc.chunks.map((x, j) =>
                          j === i
                            ? {
                                ...x,
                                categories: e.target.value
                                  .split(",")
                                  .map((s) => s.trim()),
                              }
                            : x,
                        ),
                      })
                    }
                  />
                </Field>
              </section>
            ))}
            <div className="modal-actions">
              <a
                className="button"
                href={`/api/documents/materials/${doc.id}/file`}
              >
                <Download size={16} />
                原本
              </a>
              <button
                type="button"
                className="button danger"
                disabled={busy}
                onClick={() => {
                  if (!window.confirm(
                    `「${doc.name}」を資料一覧から削除しますか？\n生成済み問題と保存済みの根拠抜粋は残ります。元ファイルは削除されます。この資料を使うレシピは資料設定の更新が必要です。`,
                  )) return;
                  perform(async () => {
                    await api(`/documents/materials/${doc.id}`, { method: "DELETE" });
                    setSelectedMaterialIds((current) =>
                      current === null
                        ? null
                        : current.filter((id) => id !== doc.id),
                    );
                    setDoc(null);
                    await refresh();
                  });
                }}
              >
                <Trash2 size={16} />
                削除
              </button>
              <button className="button primary" disabled={busy}>
                確認して保存
              </button>
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </form>
        </Modal>
      )}
      {recipe && (
        <Modal title="出題レシピを編集" onClose={() => setRecipe(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              perform(async () => {
                await api("/recipes" + (recipe.id ? "/" + recipe.id : ""), {
                  method: recipe.id ? "PUT" : "POST",
                  body: JSON.stringify(recipe),
                });
                setRecipe(null);
                await refresh();
              });
            }}
          >
            <Field label="レシピ名">
              <input
                required
                value={recipe.name}
                onChange={(e) => setRecipe({ ...recipe, name: e.target.value })}
              />
            </Field>
            <Field label="資料カテゴリ">
              <input
                required
                list="cats"
                value={recipe.category}
                onChange={(e) =>
                  setRecipe({ ...recipe, category: e.target.value })
                }
              />
              <datalist id="cats">
                {[
                  ...new Set([
                    ...categories,
                    ...data.materials.flatMap((m) =>
                      m.chunks.flatMap((c) => c.categories || []),
                    ),
                  ]),
                ].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </datalist>
            </Field>
            <fieldset className="source-picker">
              <legend>問題生成に使用する資料</legend>
              <p className="muted">
                資料を指定すると、その資料のうち上のカテゴリに一致する範囲だけを根拠にします。未選択の場合はカテゴリに一致する全資料を使用します。
              </p>
              {(recipe.material_ids || []).filter(
                (id) => !data.materials.some((material) => material.id === id),
              ).map((id) => (
                <label className="source-option" key={id}>
                  <input
                    type="checkbox"
                    checked
                    onChange={() => setRecipe({
                      ...recipe,
                      material_ids: (recipe.material_ids || []).filter(
                        (selectedId) => selectedId !== id,
                      ),
                    })}
                  />
                  <span>
                    <strong>削除済み資料 · …{id.slice(-8)}</strong>
                    <small>選択を外してください。資料指定が空になると全資料が対象になります。</small>
                  </span>
                </label>
              ))}
              {data.materials.length ? (
                data.materials.map((material) => {
                  const selected = (recipe.material_ids || []).includes(
                    material.id,
                  );
                  return (
                    <label className="source-option" key={material.id}>
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(e) =>
                          setRecipe({
                            ...recipe,
                            material_ids: e.target.checked
                              ? [
                                  ...(recipe.material_ids || []),
                                  material.id,
                                ]
                              : (recipe.material_ids || []).filter(
                                  (id) => id !== material.id,
                                ),
                          })
                        }
                      />
                      <span>
                        <strong>{material.name}</strong>
                        <small>
                          {material.chunks.length}チャンク ·{" "}
                          {material.categories.join("、") || "未分類"}
                        </small>
                      </span>
                    </label>
                  );
                })
              ) : (
                <p className="muted">先に試験範囲の資料を登録してください。</p>
              )}
            </fieldset>
            <div className="form-grid">
              <Field label="問題形式">
                <select
                  value={recipe.question_type}
                  onChange={(e) =>
                    setRecipe({
                      ...recipe,
                      question_type: e.target.value as Recipe["question_type"],
                    })
                  }
                >
                  {Object.entries(typeNames).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="難易度">
                <select
                  value={recipe.difficulty}
                  onChange={(e) =>
                    setRecipe({ ...recipe, difficulty: e.target.value })
                  }
                >
                  {["基礎", "標準", "応用"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </Field>
              {(
                [
                  ["major_count", "大問数", 1, 5],
                  ["sub_count", "大問ごとの小問数", 1, 10],
                  ["body_length", "問題文の文字数", 20, 2000],
                  ["choice_count", "選択肢数", 2, 8],
                  ["blank_count", "空欄数", 1, 8],
                  ["score_weight", "参考配点", 1, 100],
                ] as const
              ).map(([key, label, min, max]) => (
                <Field key={key} label={label}>
                  <input
                    type="number"
                    required
                    min={min}
                    max={max}
                    value={recipe[key]}
                    onChange={(e) =>
                      setRecipe({ ...recipe, [key]: Number(e.target.value) })
                    }
                  />
                </Field>
              ))}
            </div>
            <Field label="解答方式">
              <input
                value={recipe.answer_type}
                onChange={(e) =>
                  setRecipe({ ...recipe, answer_type: e.target.value })
                }
              />
            </Field>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={recipe.word_bank}
                onChange={(e) =>
                  setRecipe({ ...recipe, word_bank: e.target.checked })
                }
              />
              語群を含める
            </label>
            <Field label="追加の生成指示">
              <textarea
                rows={4}
                value={recipe.generation_instruction}
                onChange={(e) =>
                  setRecipe({
                    ...recipe,
                    generation_instruction: e.target.value,
                  })
                }
                placeholder="例：実務での判断を問う問題にしてください"
              />
            </Field>
            <div className="modal-actions">
              <button className="button primary" disabled={busy}>
                レシピを保存
              </button>
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </form>
        </Modal>
      )}
    </div>
  );
}
function Heading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}
function Stat({
  title,
  value,
  suffix,
  detail,
  icon,
}: {
  title: string;
  value: number | string;
  suffix: string;
  detail: string;
  icon: React.ReactNode;
}) {
  return (
    <section className="panel stat">
      <div>
        <span>{title}</span>
        <span className="stat-icon">{icon}</span>
      </div>
      <strong>
        {value}
        <small>{suffix}</small>
      </strong>
      <p>{detail}</p>
    </section>
  );
}
function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="field">
      {label}
      {children}
    </label>
  );
}
function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement;
    ref.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const els = ref.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input,textarea,select,a[href]",
        );
        if (!els?.length) return;
        const first = els[0],
          last = els[els.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === ref.current)
        ) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      before?.focus();
    };
  }, []);
  return (
    <div className="modal-backdrop">
      <div
        ref={ref}
        tabIndex={-1}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="icon-button" aria-label="閉じる" onClick={onClose}>
            <X />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
if ("serviceWorker" in navigator)
  navigator.serviceWorker.register("/sw.js").catch(() => {});
createRoot(document.getElementById("root")!).render(<App />);
