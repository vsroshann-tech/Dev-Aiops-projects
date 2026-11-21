export type Severity = 'high' | 'medium' | 'low';

export interface UserPublic {
  id: number;
  email: string;
  created_at?: string | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: UserPublic;
}

export interface ResourceGroupItem {
  name: string;
  arn?: string | null;
  description?: string | null;
  source: 'aws_resource_groups' | 'cloudformation_stack' | 'tag_filter' | string;
  region: string;
  resource_count?: number | null;
}

export interface ResourceGroupsResponse {
  account_id: string;
  region: string;
  count: number;
  resource_groups: ResourceGroupItem[];
  warnings: string[];
}

export interface ResourceItem {
  arn: string;
  resource_type: string;
  service: string;
  name: string;
  resource_id: string;
  region: string;
  location: string;
  instance_type?: string | null;
  sku?: string | null;
  state?: string | null;
  tags: Record<string, string>;
  details: Record<string, unknown>;
}

export interface ServiceCostItem {
  service: string;
  amount: number;
  unit: string;
}

export interface CostSummary {
  period_start: string;
  period_end: string;
  total_cost: number;
  currency: string;
  by_service: ServiceCostItem[];
  filter_applied?: string | null;
  warning?: string | null;
}

export interface CostIssue {
  title: string;
  resource_name: string;
  resource_id: string;
  resource_arn?: string | null;
  resource_type: string;
  region: string;
  issue_type?: string;
  category: string;
  severity: Severity;
  description: string;
  cost_impact?: string | null;
  recommendation: string;
  estimated_monthly_savings_usd: number;
  fix_commands: string[];
}

export interface EstimatedSavings {
  monthly_usd: number;
  annual_usd: number;
  currency: string;
  display: string;
  notes: string;
}

export interface FixCommand {
  command: string;
  description: string;
  resource_id: string;
  severity: Severity;
  requires_downtime: boolean;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

export interface AIAnalysisResult {
  summary: string;
  issue_count: number;
  severity_counts: Record<string, number>;
  issues: CostIssue[];
  estimated_savings: EstimatedSavings;
  actionable_fix_commands: FixCommand[];
  model: string;
  token_usage?: TokenUsage | null;
}

export interface AnalyzeResponse {
  analysis_id: string;
  status: string;
  stored_in_db: boolean;
  resource_group: string;
  filter_type: string;
  account_id: string;
  region: string;
  resource_count: number;
  resources: ResourceItem[];
  summary_by_service: Record<string, number>;
  cost_summary?: CostSummary | null;
  analysis: AIAnalysisResult;
  warnings: string[];
  scanned_at: string;
}

export interface HistoryItem {
  id: string;
  user_id?: number | null;
  resource_group: string;
  resources_scanned: number;
  issues_found: number;
  estimated_savings: string;
  analysis_result: Partial<AnalyzeResponse>;
  status: string;
  created_at?: string | null;
}

export interface HistoryResponse {
  count: number;
  analyses: HistoryItem[];
}

export interface ProgressEvent {
  analysis_id: string;
  step: number;
  total_steps: number;
  stage: string;
  message: string;
  progress: number;
  status: 'in_progress' | 'completed' | 'failed';
  timestamp: string;
}
