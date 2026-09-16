export type Question = {
  id: string;
  body: string;
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
  reference_past_question_id: string;
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
  year: string;
  categories: string[];
  chunks: Record<string, any>[];
  questions: Record<string, any>[];
  warnings: string[];
  status: string;
  version: number;
  analysis_method: string;
  analysis_provider?: string;
  chunking?: {
    max_chars: number;
    source_chunk_count: number;
    chunk_count: number;
    analysis_batch_count: number;
    auto_split: boolean;
  };
};
export type Data = {
  questions: Question[];
  materials: Doc[];
  exams: Doc[];
  recipes: Recipe[];
  categories: { id: string; name: string }[];
  sets: { id: string; name: string }[];
  provider: string;
};
export const typeNames = {
  choice: "選択問題",
  blank: "穴埋め",
  word: "単語",
  short: "短文記述",
};
