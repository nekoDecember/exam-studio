export type Question = {
  id: string;
  body: string;
  tested_concept?: string;
  answer_target?: string;
  question_goal?: string;
  category: string;
  question_type: "choice" | "blank" | "word" | "short";
  choices: string[];
  answer: string;
  accepted_answers: string[];
  explanation: string;
  source_references: Record<string, any>[];
  warnings: string[];
  status: "active" | "deleted";
  favorite: boolean;
  note: string;
  recipe_id: string;
  question_set_id: string;
  parent: string;
  score_weight: number;
  grading_rubric: string;
  quality_version?: string;
  quality_review?: Record<string, any>;
};
export type Result = {
  correct: boolean | null;
  score: number | null;
  reason: string;
  reference: boolean;
};
export type Attempt = {
  id: string;
  name: string;
  questions: Question[];
  index: number;
  answers: Record<string, string>;
  results: Record<string, Result>;
  flags: Record<string, boolean>;
  notes: Record<string, string>;
  revision: number;
  updated_at: string;
};
export type Recipe = {
  id: string;
  name: string;
  category: string;
  question_type: Question["question_type"];
  major_count: number;
  sub_count: number;
  body_length: number;
  choice_count: number;
  blank_count: number;
  word_bank: boolean;
  answer_type: string;
  difficulty: string;
  score_weight: number;
  generation_instruction: string;
  material_ids: string[];
};
export type Doc = {
  id: string;
  name: string;
  file_type?: string;
  categories: string[];
  chunks: Record<string, any>[];
  warnings: string[];
  status: string;
  version: number;
  analysis_method: string;
  analysis_provider?: string;
  source_url?: string;
  chunking?: {
    max_chars: number;
    source_chunk_count: number;
    chunk_count: number;
    analysis_batch_count: number;
    generation_batch_count?: number;
    generation_location_count?: number;
    auto_split: boolean;
  };
};
export type Data = {
  questions: Question[];
  materials: Doc[];
  recipes: Recipe[];
  categories: { id: string; name: string }[];
  sets: { id: string; name: string }[];
  provider: string;
};
export type GenerationJob = {
  id: string;
  kind: "direct" | "recipe";
  status: "queued" | "running" | "complete" | "partial" | "failed";
  total: number;
  completed: number;
  requested_count?: number;
  accepted_count?: number;
  reviewed_count?: number;
  rejected_count?: number;
  stage?: "waiting" | "generating" | "reviewing" | "saving" | "finished";
  stop_reason?: "target_reached" | "candidate_exhausted" | "attempt_limit";
  set_ids: string[];
  error?: string;
  created_at?: string;
  updated_at?: string;
};
export const typeNames = {
  choice: "選択問題",
  blank: "穴埋め",
  word: "単語",
  short: "短文記述",
};
