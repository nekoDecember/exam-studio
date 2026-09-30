import { Check, Sparkles, X } from "lucide-react";
import type { GenerationJob } from "./types";

export function GenerationStatus({ job, canPractice, onReview, onPractice, onDismiss }: {
  job: GenerationJob;
  canPractice: boolean;
  onReview: () => void;
  onPractice: () => void;
  onDismiss: () => void;
}) {
  const running = job.status === "queued" || job.status === "running";
  const saved = job.accepted_count ?? job.completed;
  const target = job.requested_count ?? job.total;
  const unit = job.requested_count !== undefined || job.kind === "direct" ? "問" : "セット";
  const stage = job.status === "queued" ? "開始を待っています" : job.stage === "reviewing"
    ? "問題の内容と根拠を確認しています" : job.stage === "saving"
      ? "問題を保存しています" : "資料から問題を作っています";
  const title = running ? stage : job.status === "partial"
    ? saved ? `${target}${unit}中${saved}${unit}を作成しました` : "出題に適した問題を作成できませんでした"
    : job.status === "failed" ? "問題生成が中断されました" : `${saved}${unit}の問題を作成しました`;
  return <section className={`panel generation-job ${job.status}`} aria-live="polite">
    <div className="generation-job-copy">
      <span className={`import-status ${job.status}`}>
        {running ? <Sparkles size={14} /> : job.status !== "failed" ? <Check size={14} /> : null}
        {running ? "作成中" : job.status === "partial" ? "作成終了" : job.status === "complete" ? "完了" : "中断"}
      </span>
      <h3>{title}</h3>
      {running ? <>
        <p>{saved} / {target} {unit}を保存済み。画面を移動しても作成は続きます。</p>
        <div className="progress-track" role="progressbar" aria-label="保存した問題の割合"
          aria-valuemin={0} aria-valuemax={target} aria-valuenow={saved}>
          <i style={{ width: `${Math.min(100, Math.round(saved / Math.max(1, target) * 100))}%` }} />
        </div>
      </> : job.status === "partial" ? <p>
        {job.stop_reason === "attempt_limit"
          ? "十分な品質を確認できる問題が揃わなかったため、作成を終了しました。"
          : "学習に適した題材が不足したため、作成を終了しました。"}
        {saved > 0 ? " 作成できた問題はそのまま演習できます。" : " 資料を追加するか、問題形式を変えてお試しください。"}
      </p> : job.status === "failed" ? <p>{job.error}{saved > 0 && " 保存済みの問題は演習できます。"}</p>
        : <p>正解と資料の根拠を確認して、演習を始めましょう。</p>}
    </div>
    {!!job.set_ids.length && <div className="generation-job-actions">
      <button className="button" onClick={onReview}>問題を確認</button>
      {canPractice && <button className="button primary" onClick={onPractice}>演習を始める</button>}
    </div>}
    {!running && <button className="icon-button job-dismiss" aria-label="生成結果の表示を閉じる" onClick={onDismiss}><X size={18} /></button>}
  </section>;
}
