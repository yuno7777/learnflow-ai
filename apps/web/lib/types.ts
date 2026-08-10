/** Types mirroring the FastAPI response models under /api/v1. */

export type Role =
  | "platform_admin"
  | "org_admin"
  | "instructor"
  | "reviewer"
  | "support_agent"
  | "learner";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: Record<string, unknown> };
  request_id: string | null;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_platform_admin: boolean;
  last_login_at: string | null;
}

export interface Membership {
  organization_id: string;
  organization_name: string;
  organization_slug: string;
  role: Role;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
  organization_id: string;
  role: Role;
  memberships: Membership[];
}

export interface Me {
  user: User;
  organization_id: string;
  role: Role;
  /** Set only for the learner role: the learner record this login speaks for. */
  learner_id: string | null;
  permissions: string[];
  memberships: Membership[];
}

export interface Learner {
  id: string;
  email: string;
  full_name: string;
  status: string;
  workspace_id: string | null;
  external_ref: string | null;
  last_active_at: string | null;
  reminders_sent: number;
  profile: Record<string, unknown>;
  created_at: string;
}

export interface Enrollment {
  id: string;
  course_id: string;
  course_title: string | null;
  status: string;
  progress: number;
  completed_lesson_ids: string[];
  completed_at: string | null;
  created_at: string;
}

export type LearnerDetail = Learner & { enrollments: Enrollment[] };

export type DocumentStatus = "pending" | "processing" | "indexed" | "failed" | "deleted";
export type PipelineStage =
  | "upload"
  | "validation"
  | "extraction"
  | "cleaning"
  | "metadata"
  | "chunking"
  | "embedding"
  | "indexing"
  | "quality"
  | "completed";

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  embedding_model: string;
  chunk_size_tokens: number;
  chunk_overlap_tokens: number;
  document_count: number;
  chunk_count: number;
  created_at: string;
}

export interface PipelineRun {
  id: string;
  stage: PipelineStage;
  status: string;
  attempt: number;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
  details: Record<string, unknown>;
}

export interface DocumentSummary {
  id: string;
  knowledge_base_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: DocumentStatus;
  stage: PipelineStage;
  progress: number;
  error: string | null;
  page_count: number | null;
  word_count: number | null;
  detected_language: string | null;
  indexed_at: string | null;
  created_at: string;
}

export type DocumentDetail = DocumentSummary & {
  chunk_count: number;
  runs: PipelineRun[];
  job_id?: string | null;
};

export interface Citation {
  chunk_id: string;
  document_id: string;
  document_name: string;
  snippet: string;
  score: number;
  page: number | null;
  section: string | null;
}

export interface RagAnswer {
  answer: string;
  refused: boolean;
  citations: Citation[];
  confidence: number;
  model: string | null;
  provider: string | null;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  retrieval_latency_ms: number;
  llm_latency_ms: number;
  conversation_id: string | null;
  message_id: string | null;
}

export type CourseStatus =
  | "draft"
  | "generating"
  | "review"
  | "published"
  | "archived"
  | "failed";

export interface Lesson {
  id: string;
  title: string;
  position: number;
  summary: string | null;
  content: string | null;
  exercise: string | null;
  estimated_minutes: number | null;
}

export interface CourseModule {
  id: string;
  title: string;
  position: number;
  summary: string | null;
  learning_objectives: string[];
  lessons: Lesson[];
}

export interface Course {
  id: string;
  title: string;
  slug: string;
  description: string | null;
  status: CourseStatus;
  knowledge_base_id: string | null;
  learning_objectives: string[];
  model_used: string | null;
  prompt_version: number | null;
  published_at: string | null;
  created_at: string;
}

export type CourseDetail = Course & {
  modules: CourseModule[];
  enrollment_count: number;
  job_id?: string | null;
};

export type QuestionType = "multiple_choice" | "true_false" | "short_answer" | "scenario";

export interface Question {
  id: string;
  position: number;
  type: QuestionType;
  prompt: string;
  options: string[];
  difficulty: string;
  learning_objective: string | null;
  points: number;
  correct_answer: string | null;
  explanation: string | null;
  source_reference: string | null;
  confidence: number | null;
  rubric: string | null;
}

export interface Assessment {
  id: string;
  course_id: string | null;
  title: string;
  description: string | null;
  passing_score: number;
  difficulty: string;
  topics: string[];
  is_published: boolean;
  model_used: string | null;
  validation_report: Record<string, unknown>;
  created_at: string;
}

export type AssessmentDetail = Assessment & { questions: Question[]; job_id?: string | null };

export type EvaluationStatus =
  | "pending"
  | "ai_completed"
  | "needs_review"
  | "approved"
  | "rejected"
  | "failed";

export interface Evaluation {
  id: string;
  submission_id: string;
  status: EvaluationStatus;
  score: number | null;
  max_score: number | null;
  confidence: number | null;
  rationale: string | null;
  strengths: string[];
  missing_concepts: string[];
  suggested_resources: string[];
  per_question: Array<Record<string, unknown>>;
  requires_human_review: boolean;
  reviewed_by: string | null;
  reviewed_at: string | null;
  reviewer_notes: string | null;
  model_used: string | null;
  error: string | null;
  created_at: string;
}

export interface Submission {
  id: string;
  assessment_id: string;
  learner_id: string;
  status: string;
  auto_score: number | null;
  answers: Array<{ question_id: string; answer: string }>;
  submitted_at: string;
  created_at: string;
}

export type SubmissionDetail = Submission & { evaluation: Evaluation | null; job_id?: string | null };

export type ExecutionStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface Workflow {
  id: string;
  key: string;
  name: string;
  description: string | null;
  template: string;
  trigger_type: string;
  trigger_event: string | null;
  is_enabled: boolean;
  n8n_webhook_path: string | null;
  n8n_workflow_id: string | null;
  config: Record<string, unknown>;
  max_retries: number;
  created_at: string;
}

export interface WorkflowStep {
  id: string;
  position: number;
  name: string;
  kind: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  attempt: number;
  output: Record<string, unknown>;
  error: string | null;
}

export interface Execution {
  id: string;
  workflow_id: string;
  workflow_name?: string | null;
  status: ExecutionStatus;
  trigger_type: string;
  triggered_by: string | null;
  n8n_execution_id: string | null;
  retry_count: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  learner_id: string | null;
  course_id: string | null;
  error: string | null;
  created_at: string;
}

export type ExecutionDetail = Execution & {
  workflow_key?: string | null;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  steps: WorkflowStep[];
};

export interface Ticket {
  id: string;
  subject: string;
  body: string;
  learner_id: string | null;
  status: string;
  category: string | null;
  urgency: string | null;
  sentiment: string | null;
  ai_confidence: number | null;
  ai_explanation: string | null;
  suggested_response: string | null;
  kb_references: Array<Record<string, unknown>>;
  escalated: boolean;
  assigned_to: string | null;
  resolved_at: string | null;
  created_at: string;
}

export interface Overview {
  total_learners: number;
  active_learners: number;
  course_completion_rate: number;
  average_assessment_score: number | null;
  open_support_tickets: number;
  documents_indexed: number;
  ai_requests: number;
  tokens_consumed: number;
  estimated_cost_usd: number;
  workflow_success_rate: number;
  workflow_failure_rate: number;
  average_workflow_duration_ms: number | null;
  rag_retrieval_latency_ms: number | null;
  llm_response_latency_ms: number | null;
  includes_demo_data: boolean;
}

export interface TimeseriesPoint {
  date: string;
  value: number;
  secondary: number | null;
}

export interface Timeseries {
  metric: string;
  points: TimeseriesPoint[];
}

export interface UsageSummary {
  includes_demo_data: boolean;
  demo_requests: number;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  by_model: Array<{
    provider: string;
    model: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
  }>;
  by_operation: Array<{
    operation: string;
    requests: number;
    failures: number;
    tokens: number;
    estimated_cost_usd: number;
    avg_latency_ms: number | null;
  }>;
  daily: TimeseriesPoint[];
}

export interface WorkflowStats {
  workflow_id: string;
  key: string;
  name: string;
  total: number;
  succeeded: number;
  failed: number;
  running: number;
  success_rate: number;
  avg_duration_ms: number | null;
  last_run_at: string | null;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
  key?: string;
}

export interface AuditEntry {
  id: string;
  actor_type: string;
  actor_label: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  request_id: string | null;
  ip_address: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Job {
  id: string;
  kind: string;
  status: string;
  progress: number;
  message: string | null;
  resource_type: string | null;
  resource_id: string | null;
  attempts: number;
  max_attempts: number;
  error: string | null;
  result: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface Integrations {
  llm: { provider: string; model: string; fallback: string | null; configured: boolean };
  embeddings: { provider: string; model: string };
  vector_store: { engine: string; healthy: boolean; vectors: number };
  redis: { healthy: boolean };
  n8n: { base_url: string; healthy: boolean };
  email: { adapter: string };
}

export interface InactiveLearner {
  id: string;
  email: string;
  full_name: string;
  days_inactive: number | null;
  reminders_sent: number;
  last_active_at: string | null;
  course_id: string | null;
  course_title: string | null;
  progress: number;
}

export interface InactiveLearnersResponse {
  threshold_days: number;
  max_reminders: number;
  learners: InactiveLearner[];
  generated_at: string;
}

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  created_at: string;
}

export interface TransformJob {
  output_type: string;
  job_id: string;
  status: string;
}

export interface AssistantReply {
  answer: string;
  conversation_id: string;
  message_id: string;
  tool_calls: Array<{ name: string; arguments: Record<string, unknown> }>;
  citations: Citation[];
  model: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
}

export interface LearnerAccessResult {
  learner_id: string;
  user_id: string;
  email: string;
  account_created: boolean;
}
