import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BookOpen,
  LayoutDashboard,
  Library,
  Files,
  ScrollText,
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
  saveAttempt,
  syncAttempts,
  newAttempt,
  gradeLocal,
} from "./store";
import type { Data, Question, Attempt, Recipe, Doc } from "./types";
import { typeNames } from "./types";
import "./style.css";

type Page =
  | "home"
  | "practice"
  | "bank"
  | "materials"
  | "exams"
  | "recipes"
  | "analytics";
const empty: Data = {
  questions: [],
  materials: [],
  exams: [],
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
  ["exams", "過去問・出題形式", ScrollText],
  ["recipes", "出題レシピ", SlidersHorizontal],
  ["analytics", "学習の記録", ChartNoAxesCombined],
];
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
    [majorFilter, setMajorFilter] = useState("すべて"),
    [editor, setEditor] = useState<Question | null>(null),
    [doc, setDoc] = useState<Doc | null>(null),
    [recipe, setRecipe] = useState<Recipe | null>(null),
    [batch, setBatch] = useState(1);
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
    setData(d);
    await cacheData(d);
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
      } catch {
        if (live)
          setError(
            "端末保存を利用できません。ブラウザのストレージ設定を確認してください。",
          );
      } finally {
        if (live) setReady(true);
      }
    })();
    const sync = () =>
      syncAttempts()
        .then(() => setStatus("同期済み"))
        .catch(() => setStatus("オフライン・端末に保存"));
    window.addEventListener("online", sync);
    const interval = setInterval(sync, 15000);
    return () => {
      live = false;
      clearInterval(interval);
      window.removeEventListener("online", sync);
    };
  }, []);
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
              .then(() => {
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
  const filtered = data.questions.filter(
    (q) =>
      (setFilterId === "すべて" || q.question_set_id === setFilterId) &&
      (majorFilter === "すべて" || q.parent === majorFilter) &&
      (filter === "deleted" ? q.status === "deleted" : q.status === "active") &&
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
    await api("/questions" + (value.id ? "/" + value.id : ""), {
      method: value.id ? "PUT" : "POST",
      body: JSON.stringify(value),
    });
    setEditor(null);
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
    return {
      id: "",
      name: "新しい出題レシピ",
      category: categories[0] || "未分類",
      reference_past_question_id: "",
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
    };
  }
  async function upload(file: File, kind: string) {
    const f = new FormData();
    f.append("file", file);
    f.append("kind", kind);
    const d = await api("/uploads", { method: "POST", body: f });
    await refresh();
    setDoc(d);
  }
  const pageTitle = nav.find((n) => n[0] === page)![1];
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
                        onClick={() => go("recipes")}
                      >
                        <span className="action-icon lavender">
                          <Sparkles />
                        </span>
                        <div>
                          <strong>資料から問題をつくる</strong>
                          <p>今年の試験範囲と過去問の形式を組み合わせる</p>
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
                                  {s.sheet_name} {s.cell_range}
                                </summary>
                                <p className="pre-wrap">{s.text}</p>
                                <a
                                  href={`/api/documents/materials/${s.material_id}/file`}
                                >
                                  元の資料をダウンロード
                                </a>
                              </details>
                            ))}
                          </div>
                        )}
                        <div className="question-tools">
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
                          setSetFilterId(e.target.value);
                          setMajorFilter("すべて");
                        }}
                      >
                        <option value="すべて">すべてのセット</option>
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
                              .filter(
                                (q) =>
                                  setFilterId === "すべて" ||
                                  q.question_set_id === setFilterId,
                              )
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
                        ["deleted", "削除済み"],
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
                      {filter !== "deleted" && (
                        <button
                          className="text-button"
                          onClick={() => start(filtered, "選択した条件で演習")}
                        >
                          この条件で演習 <Play size={14} />
                        </button>
                      )}
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
                            aria-label={
                              x.status === "deleted"
                                ? "問題を復元"
                                : "問題を削除"
                            }
                            onClick={() =>
                              perform(() =>
                                saveQ({
                                  ...x,
                                  status:
                                    x.status === "deleted"
                                      ? "active"
                                      : "deleted",
                                }),
                              )
                            }
                          >
                            {x.status === "deleted" ? (
                              <RotateCcw size={18} />
                            ) : (
                              <Trash2 size={18} />
                            )}
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                </>
              )}
              {(page === "materials" || page === "exams") && (
                <>
                  <Heading
                    eyebrow={
                      page === "materials" ? "STUDY MATERIALS" : "PAST EXAMS"
                    }
                    title={
                      page === "materials"
                        ? "今年の資料を、学びの土台に。"
                        : "過去問から、出題のかたちを。"
                    }
                    description={
                      page === "materials"
                        ? "問題の内容・正解・解説の根拠となる資料を登録します。"
                        : "過去問は、出題形式・構成・文体の参考に使用します。"
                    }
                  />
                  <label className={"upload-zone " + (busy ? "disabled" : "")}>
                    <span className="upload-icon">
                      <Upload size={26} />
                    </span>
                    <h3>
                      {busy ? "資料を解析しています…" : "ファイルを選んで登録"}
                    </h3>
                    <p>PDF・Excel (.xlsx)・画像・テキスト / 最大20MB</p>
                    <span className="button">
                      ファイルを選択
                      <Plus size={16} />
                    </span>
                    <input
                      aria-label="資料ファイル"
                      type="file"
                      disabled={busy}
                      accept=".pdf,.xlsx,.png,.jpg,.jpeg,.webp,.txt"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) perform(() => upload(f, page));
                        e.target.value = "";
                      }}
                    />
                  </label>
                  <div className="section-heading document-heading">
                    <h3>登録した{page === "materials" ? "資料" : "過去問"}</h3>
                    <span className="muted">{data[page].length} 件</span>
                  </div>
                  <div className="document-grid">
                    {data[page].map((d) => (
                      <button
                        key={d.id}
                        className="panel document-card"
                        onClick={() => setDoc(structuredClone(d))}
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
                  {!data[page].length && (
                    <div className="empty subtle">
                      まだ資料はありません。最初のファイルを登録しましょう。
                    </div>
                  )}
                </>
              )}
              {page === "recipes" && (
                <>
                  <Heading
                    eyebrow="GENERATION RECIPES"
                    title="あなたの試験に合う、出題を。"
                    description="今年の資料のカテゴリと、過去問の出題形式を組み合わせます。"
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
                            disabled={busy}
                            onClick={() =>
                              perform(async () => {
                                await api("/generate", {
                                  method: "POST",
                                  body: JSON.stringify({
                                    recipe_id: r.id,
                                    count: batch,
                                  }),
                                });
                                await refresh();
                                go("bank");
                              })
                            }
                          >
                            <Sparkles size={16} />
                            {busy ? "生成中…" : "生成する"}
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
                  {s.material_name} {s.page_number} {s.sheet_name}{" "}
                  {s.cell_range}
                </summary>
                <p>{s.text}</p>
                <a href={`/api/documents/materials/${s.material_id}/file`}>
                  根拠資料をダウンロード
                </a>
              </details>
            ))}
            <div className="modal-actions">
              {editor.recipe_id && (
                <button
                  type="button"
                  disabled={busy}
                  className="button"
                  onClick={() =>
                    perform(async () => {
                      await api("/questions/" + editor.id + "/regenerate", {
                        method: "POST",
                      });
                      setEditor(null);
                      await refresh();
                    })
                  }
                >
                  再生成して追加
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
                  `/documents/${page === "exams" ? "exams" : "materials"}/${doc.id}`,
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
            {page === "exams" ? (
              <>
                <Field label="試験年度">
                  <input
                    value={doc.year}
                    onChange={(e) => setDoc({ ...doc, year: e.target.value })}
                  />
                </Field>
                <p className="muted">
                  大問・小問、選択肢、空欄、語群、正解、文体を確認してください。正解の記載がない場合は手動で補います。
                </p>
                {doc.questions.map((pq, i) => (
                  <section className="chunk" key={pq.id || i}>
                    <h4>問 {pq.question_number}</h4>
                    <div className="form-grid">
                      <Field label="問題番号">
                        <input
                          value={pq.question_number || ""}
                          onChange={(e) =>
                            setDoc({
                              ...doc,
                              questions: doc.questions.map((x, j) =>
                                i === j
                                  ? { ...x, question_number: e.target.value }
                                  : x,
                              ),
                            })
                          }
                        />
                      </Field>
                      <Field label="親問題のID（小問の場合）">
                        <input
                          value={pq.parent_question_id || ""}
                          onChange={(e) =>
                            setDoc({
                              ...doc,
                              questions: doc.questions.map((x, j) =>
                                i === j
                                  ? { ...x, parent_question_id: e.target.value }
                                  : x,
                              ),
                            })
                          }
                        />
                      </Field>
                    </div>
                    <Field label="過去問の本文">
                      <textarea
                        rows={4}
                        value={pq.raw_text}
                        onChange={(e) =>
                          setDoc({
                            ...doc,
                            questions: doc.questions.map((x, j) =>
                              i === j
                                ? {
                                    ...x,
                                    raw_text: e.target.value,
                                    style_profile_json: {
                                      ...x.style_profile_json,
                                      length: e.target.value.length,
                                    },
                                  }
                                : x,
                            ),
                          })
                        }
                      />
                    </Field>
                    <Field label="過去問の選択肢（1行に1つ）">
                      <textarea
                        rows={3}
                        value={(pq.choices || []).join("\n")}
                        onChange={(e) =>
                          setDoc({
                            ...doc,
                            questions: doc.questions.map((x, j) =>
                              i === j
                                ? { ...x, choices: e.target.value.split("\n") }
                                : x,
                            ),
                          })
                        }
                      />
                    </Field>
                    <Field label="過去問の正解">
                      <input
                        value={pq.answer || ""}
                        onChange={(e) =>
                          setDoc({
                            ...doc,
                            questions: doc.questions.map((x, j) =>
                              i === j ? { ...x, answer: e.target.value } : x,
                            ),
                          })
                        }
                      />
                    </Field>
                    <p className="muted">
                      本文 {pq.style_profile_json?.length || pq.raw_text.length}{" "}
                      文字 · 空欄 {pq.structure_json?.blank_count || 0} 個
                    </p>
                  </section>
                ))}
                <details>
                  <summary>構造・語群・文体の詳細を編集</summary>
                  <JsonEditor
                    label="詳細データ（JSON）"
                    value={doc.questions}
                    onChange={(questions) => setDoc({ ...doc, questions })}
                  />
                </details>
              </>
            ) : (
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
            )}
            {doc.warnings.map((w, i) => (
              <div className="warning" key={i}>
                {w}
              </div>
            ))}
            <p className="muted">
              解析方式：
              {doc.analysis_method === "mock"
                ? "標準抽出（AI未接続）"
                : "AI解析"}{" "}
              · 抽出結果は編集して確定できます。
            </p>
            {doc.chunks.map((c, i) => (
              <section className="chunk" key={c.id}>
                <h4>
                  {c.page_number
                    ? `ページ ${c.page_number}`
                    : `チャンク ${i + 1}`}{" "}
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
                {page === "materials" && (
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
                )}
              </section>
            ))}
            <div className="modal-actions">
              <a
                className="button"
                href={`/api/documents/${page === "exams" ? "exams" : "materials"}/${doc.id}/file`}
              >
                <Download size={16} />
                原本
              </a>
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
            <Field label="参考にする過去問の形式">
              <select
                value={recipe.reference_past_question_id}
                onChange={(e) =>
                  setRecipe({
                    ...recipe,
                    reference_past_question_id: e.target.value,
                  })
                }
              >
                <option value="">指定しない</option>
                {data.exams.flatMap((d) =>
                  d.questions.map((p) => (
                    <option key={p.id} value={p.id}>
                      {d.name} / 問{p.question_number}
                    </option>
                  )),
                )}
              </select>
            </Field>
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
function JsonEditor({
  label,
  value,
  onChange,
}: {
  label: string;
  value: any[];
  onChange: (v: any[]) => void;
}) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (document.activeElement !== input.current) setText(JSON.stringify(value, null, 2));
  }, [value]);
  return (
    <Field label={label}>
      <textarea
        ref={input}
        className="code-input"
        rows={14}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed = JSON.parse(e.target.value);
            if (!Array.isArray(parsed)) throw Error();
            onChange(parsed);
            e.target.setCustomValidity("");
          } catch {
            e.target.setCustomValidity("正しいJSON配列を入力してください");
          }
        }}
      />
    </Field>
  );
}

if ("serviceWorker" in navigator)
  navigator.serviceWorker.register("/sw.js").catch(() => {});
createRoot(document.getElementById("root")!).render(<App />);
