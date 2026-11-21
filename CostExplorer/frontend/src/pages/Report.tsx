import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Database,
  DollarSign,
  Loader2,
  Server,
  ShieldAlert,
  Sparkles,
  Terminal,
} from 'lucide-react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { ApiError, fetchHistoryItem } from '../lib/api';
import type { AnalyzeResponse, CostIssue, Severity } from '../types';

const SEVERITY_STYLES: Record<Severity, string> = {
  high: 'border-red-500/40 bg-red-500/15 text-red-300',
  medium: 'border-yellow-500/40 bg-yellow-500/15 text-yellow-300',
  low: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
};

const ISSUE_TYPE_LABELS: Record<string, string> = {
  'over-provisioned': 'over-provisioned',
  over_provisioning: 'over-provisioned',
  right_sizing: 'over-provisioned',
  unused: 'unused',
  idle_resource: 'unused',
  misconfigured: 'misconfigured',
  misconfiguration: 'misconfigured',
  tagging: 'misconfigured',
  'wrong-pricing-tier': 'wrong-pricing-tier',
  pricing_tier: 'wrong-pricing-tier',
  storage_tiering: 'wrong-pricing-tier',
  savings_plan: 'wrong-pricing-tier',
};

function resolveIssueType(issue: CostIssue): string {
  const raw = (issue.issue_type || issue.category || 'misconfigured').toLowerCase();
  return ISSUE_TYPE_LABELS[raw] || raw.replace(/_/g, '-');
}

function CopyableCommandBlock({
  command,
  description,
  requiresDowntime,
}: {
  command: string;
  description?: string;
  requiresDowntime?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = command;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950">
      {(description || requiresDowntime) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 bg-slate-900/60 px-3.5 py-2 text-xs">
          <span className="text-slate-300">{description || 'AWS CLI Command'}</span>
          {requiresDowntime && (
            <span className="inline-flex items-center gap-1 rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-300">
              <AlertTriangle className="h-3 w-3" />
              May cause downtime
            </span>
          )}
        </div>
      )}
      <div className="flex items-start justify-between gap-3 p-3.5">
        <pre className="overflow-x-auto font-mono text-xs leading-relaxed text-emerald-300">
          <code>{command}</code>
        </pre>
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:border-amber-500/50 hover:text-amber-300"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5 text-emerald-400" />
              <span className="text-emerald-300">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
    </div>
  );
}

export default function Report() {
  const { analysisId } = useParams<{ analysisId?: string }>();
  const location = useLocation();
  const stateReport = (location.state as { report?: AnalyzeResponse } | null)?.report ?? null;

  const [report, setReport] = useState<AnalyzeResponse | null>(stateReport);
  const [savedTimestamp, setSavedTimestamp] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!stateReport && Boolean(analysisId));
  const [error, setError] = useState<string | null>(null);
  const [severityFilter, setSeverityFilter] = useState<'all' | Severity>('all');
  const [showResources, setShowResources] = useState<boolean>(false);

  useEffect(() => {
    if (stateReport) {
      setReport(stateReport);
      setLoading(false);
      setError(null);
      return;
    }

    if (!analysisId) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchHistoryItem(analysisId)
      .then((item) => {
        if (cancelled) return;
        const result = item.analysis_result as AnalyzeResponse;
        if (result && result.analysis) {
          setReport({
            ...result,
            analysis_id: result.analysis_id || item.id,
            status: result.status || item.status,
            stored_in_db: true,
          });
          setSavedTimestamp(item.created_at ?? null);
        } else {
          setError('Stored analysis record is missing detailed report data.');
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : 'Failed to load analysis report.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [analysisId, stateReport]);

  const arnLookup = useMemo(() => {
    const map = new Map<string, string>();
    for (const res of report?.resources ?? []) {
      if (res.resource_id && res.arn) {
        map.set(res.resource_id, res.arn);
      }
    }
    return map;
  }, [report]);

  const filteredIssues: CostIssue[] = useMemo(() => {
    const all = report?.analysis?.issues ?? [];
    if (severityFilter === 'all') return all;
    return all.filter((i) => i.severity === severityFilter);
  }, [report, severityFilter]);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 px-4 text-slate-400">
        <Loader2 className="h-7 w-7 animate-spin text-amber-400" />
        <p className="text-sm">Loading analysis report from Amazon RDS...</p>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-12 sm:px-6">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-8 text-center">
          <AlertCircle className="mx-auto h-10 w-10 text-red-400" />
          <h1 className="mt-4 text-lg font-semibold text-slate-100">
            {error ? 'Could not load report' : 'No report selected'}
          </h1>
          <p className="mt-1.5 text-sm text-slate-400">
            {error || 'Run a new AWS cost analysis from the Dashboard or pick one from History.'}
          </p>
          <div className="mt-6 flex items-center justify-center gap-3">
            <Link
              to="/dashboard"
              className="rounded-xl bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400"
            >
              Go to Dashboard
            </Link>
            <Link
              to="/history"
              className="rounded-xl border border-slate-700 bg-slate-950 px-4 py-2 text-sm font-medium text-slate-200 hover:border-slate-600"
            >
              View History
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const { analysis, cost_summary: costSummary } = report;
  const highCount = analysis.severity_counts?.high ?? 0;
  const mediumCount = analysis.severity_counts?.medium ?? 0;
  const lowCount = analysis.severity_counts?.low ?? 0;
  const timestamp = report.scanned_at || savedTimestamp;

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      {/* Top Breadcrumb & Metadata */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            to="/dashboard"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-slate-700 hover:text-slate-100"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Dashboard
          </Link>
          <Link to="/history" className="text-xs font-medium text-slate-400 hover:text-amber-300">
            All History
          </Link>
        </div>

        <div className="flex flex-wrap items-center gap-2 font-mono text-xs text-slate-400">
          {report.stored_in_db && (
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-emerald-300">
              <Database className="h-3 w-3" />
              Saved in RDS
            </span>
          )}
          <span className="rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1">
            Account: {report.account_id}
          </span>
          <span className="rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1">
            Region: {report.region}
          </span>
          {timestamp && (
            <span className="rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1">
              {new Date(timestamp).toLocaleString()}
            </span>
          )}
        </div>
      </div>

      {/* Report Title */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-100 sm:text-3xl">
          Cost Analysis Report: <span className="text-amber-400">{report.resource_group}</span>
        </h1>
        <p className="mt-1 text-xs text-slate-400">
          Filter type: <span className="font-mono text-slate-300">{report.filter_type}</span> ·
          Model: <span className="font-mono text-slate-300">{analysis.model}</span>
        </p>
      </div>

      {/* Top Summary Card Grid: Resources Scanned, Issues Found, Estimated Savings, 30-Day Spend */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
          <div className="flex items-center justify-between text-xs font-medium text-slate-400">
            <span>Total Resources Scanned</span>
            <Server className="h-4 w-4 text-sky-400" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-100">{report.resource_count}</p>
          <p className="mt-1 truncate text-xs text-slate-400">
            {Object.entries(report.summary_by_service || {})
              .map(([svc, cnt]) => `${svc} (${cnt})`)
              .join(', ') || 'No services'}
          </p>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
          <div className="flex items-center justify-between text-xs font-medium text-slate-400">
            <span>Issues Found</span>
            <ShieldAlert className="h-4 w-4 text-amber-400" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-100">{analysis.issue_count}</p>
          <div className="mt-2 flex items-center gap-2 text-[11px] font-medium">
            <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-red-300">
              {highCount} High
            </span>
            <span className="rounded bg-yellow-500/15 px-1.5 py-0.5 text-yellow-300">
              {mediumCount} Med
            </span>
            <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">
              {lowCount} Low
            </span>
          </div>
        </div>

        <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-5">
          <div className="flex items-center justify-between text-xs font-medium text-emerald-300">
            <span>Estimated Savings</span>
            <DollarSign className="h-4 w-4" />
          </div>
          <p className="mt-2 text-2xl font-bold text-emerald-200">
            $
            {analysis.estimated_savings.monthly_usd.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
            <span className="text-xs font-normal text-emerald-300/80"> /mo</span>
          </p>
          <p className="mt-1 text-xs text-emerald-300/75">
            $
            {analysis.estimated_savings.annual_usd.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
            /year projected
          </p>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
          <div className="flex items-center justify-between text-xs font-medium text-slate-400">
            <span>30-Day AWS Spend</span>
            <Sparkles className="h-4 w-4 text-amber-400" />
          </div>
          <p className="mt-2 text-2xl font-bold text-slate-100">
            {costSummary
              ? `$${costSummary.total_cost.toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`
              : 'N/A'}
          </p>
          <p className="mt-1 text-xs text-slate-400">
            {costSummary
              ? `${costSummary.period_start} → ${costSummary.period_end}`
              : 'Cost Explorer not included'}
          </p>
        </div>
      </div>

      {/* Warnings */}
      {report.warnings && report.warnings.length > 0 && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-200">
          <p className="font-semibold">Scanner & Cost Explorer Notices</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-amber-200/85">
            {report.warnings.map((w, idx) => (
              <li key={idx}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* AI Executive Summary */}
      <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6">
        <div className="flex items-center gap-2 text-sm font-semibold text-amber-300">
          <Sparkles className="h-4 w-4" />
          <h2>AI Executive Summary</h2>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-slate-200">{analysis.summary}</p>
        {analysis.estimated_savings.notes && (
          <p className="mt-3 border-t border-slate-800 pt-3 text-xs text-slate-400">
            <span className="font-medium text-slate-300">Pricing note:</span>{' '}
            {analysis.estimated_savings.notes}
          </p>
        )}
      </section>

      {/* Issues List */}
      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold text-slate-100">
            Issues & Recommendations ({filteredIssues.length})
          </h2>

          {/* Severity Filter Tabs */}
          <div className="inline-flex rounded-xl border border-slate-800 bg-slate-900 p-1 text-xs">
            {(['all', 'high', 'medium', 'low'] as const).map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setSeverityFilter(level)}
                className={`rounded-lg px-3 py-1.5 font-medium capitalize transition-colors ${
                  severityFilter === level
                    ? 'bg-slate-800 text-amber-300'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {level}
              </button>
            ))}
          </div>
        </div>

        {filteredIssues.length === 0 ? (
          <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-8 text-center text-sm text-slate-400">
            No {severityFilter === 'all' ? '' : `${severityFilter}-severity `}cost issues were found
            for this resource group.
          </div>
        ) : (
          <div className="space-y-4">
            {filteredIssues.map((issue, idx) => {
              const resolvedArn = issue.resource_arn || arnLookup.get(issue.resource_id);
              const issueTypeLabel = resolveIssueType(issue);
              const costImpactText =
                issue.cost_impact ||
                (issue.estimated_monthly_savings_usd > 0
                  ? `Estimated ~$${issue.estimated_monthly_savings_usd.toFixed(2)}/month (~$${(issue.estimated_monthly_savings_usd * 12).toFixed(2)}/year) in avoidable spend.`
                  : 'Configuration or governance cost impact.');

              return (
                <article
                  key={`${issue.resource_id}-${idx}`}
                  className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 shadow-lg"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${
                            SEVERITY_STYLES[issue.severity]
                          }`}
                        >
                          {issue.severity}
                        </span>
                        <span className="rounded-md border border-slate-700 bg-slate-800 px-2 py-0.5 font-mono text-[11px] text-slate-200">
                          {issueTypeLabel}
                        </span>
                        <span className="font-mono text-xs text-slate-400">
                          {issue.resource_type}
                        </span>
                      </div>
                      <h3 className="text-base font-semibold text-slate-100">{issue.title}</h3>
                      <div className="space-y-0.5 font-mono text-xs text-slate-400">
                        <div>
                          <span className="text-slate-200">{issue.resource_name}</span> (
                          {issue.resource_id}) · {issue.region}
                        </div>
                        {resolvedArn && (
                          <div className="break-all text-[11px] text-slate-500">
                            ARN: {resolvedArn}
                          </div>
                        )}
                      </div>
                    </div>

                    {issue.estimated_monthly_savings_usd > 0 && (
                      <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-right">
                        <div className="text-[10px] font-medium uppercase tracking-wider text-emerald-300/80">
                          Est. Savings
                        </div>
                        <div className="font-mono text-sm font-bold text-emerald-300">
                          ${issue.estimated_monthly_savings_usd.toFixed(2)}/mo
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="mt-4 grid gap-3 md:grid-cols-3">
                    <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-3.5">
                      <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                        Explanation
                      </div>
                      <p className="mt-1 text-sm text-slate-300">{issue.description}</p>
                    </div>

                    <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-3.5">
                      <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-400/80">
                        Cost Impact
                      </div>
                      <p className="mt-1 text-sm text-amber-100/90">{costImpactText}</p>
                    </div>

                    <div className="rounded-xl border border-slate-800/80 bg-slate-950/60 p-3.5">
                      <div className="text-[11px] font-semibold uppercase tracking-wider text-emerald-400/80">
                        Recommendation
                      </div>
                      <p className="mt-1 text-sm text-slate-200">{issue.recommendation}</p>
                    </div>
                  </div>

                  {issue.fix_commands.length > 0 && (
                    <div className="mt-4 space-y-2">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-300">
                        <Terminal className="h-3.5 w-3.5 text-amber-400" />
                        <span>AWS CLI Fix Command{issue.fix_commands.length > 1 ? 's' : ''}</span>
                      </div>
                      {issue.fix_commands.map((cmd, cIdx) => (
                        <CopyableCommandBlock key={cIdx} command={cmd} />
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>

      {/* All Actionable AWS CLI Commands */}
      {analysis.actionable_fix_commands.length > 0 && (
        <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6">
          <div className="flex items-center gap-2">
            <Terminal className="h-5 w-5 text-amber-400" />
            <h2 className="text-base font-semibold text-slate-100">
              All Actionable AWS CLI Fix Commands ({analysis.actionable_fix_commands.length})
            </h2>
          </div>
          <p className="mt-1 text-xs text-slate-400">
            Review each command before running against production AWS accounts.
          </p>
          <div className="mt-4 space-y-3">
            {analysis.actionable_fix_commands.map((item, idx) => (
              <CopyableCommandBlock
                key={`${item.resource_id}-${idx}`}
                command={item.command}
                description={`${item.description} (${item.resource_id})`}
                requiresDowntime={item.requires_downtime}
              />
            ))}
          </div>
        </section>
      )}

      {/* Collapsible Scanned Resources Inventory */}
      <section className="rounded-2xl border border-slate-800 bg-slate-900/70">
        <button
          type="button"
          onClick={() => setShowResources((v) => !v)}
          className="flex w-full items-center justify-between px-6 py-4 text-left"
        >
          <div className="flex items-center gap-2">
            <Server className="h-4 w-4 text-sky-400" />
            <span className="text-sm font-semibold text-slate-100">
              Scanned AWS Resources Inventory ({report.resources.length})
            </span>
          </div>
          {showResources ? (
            <ChevronUp className="h-4 w-4 text-slate-400" />
          ) : (
            <ChevronDown className="h-4 w-4 text-slate-400" />
          )}
        </button>

        {showResources && (
          <div className="overflow-x-auto border-t border-slate-800">
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-950/70 text-[11px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-4 py-3">Name / ID</th>
                  <th className="px-4 py-3">Resource Type</th>
                  <th className="px-4 py-3">Region / Location</th>
                  <th className="px-4 py-3">Instance Type / SKU</th>
                  <th className="px-4 py-3">State</th>
                  <th className="px-4 py-3">Tags</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {report.resources.map((res) => (
                  <tr key={res.arn} className="hover:bg-slate-800/30">
                    <td className="px-4 py-3 font-medium text-slate-200">
                      <div>{res.name}</div>
                      <div className="font-mono text-[11px] text-slate-500">{res.resource_id}</div>
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-300">{res.resource_type}</td>
                    <td className="px-4 py-3 font-mono text-slate-300">{res.location}</td>
                    <td className="px-4 py-3 font-mono text-amber-300">
                      {res.sku || res.instance_type || '—'}
                    </td>
                    <td className="px-4 py-3 text-slate-300">{res.state || '—'}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(res.tags || {})
                          .slice(0, 4)
                          .map(([k, v]) => (
                            <span
                              key={k}
                              className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
                            >
                              {k}={v}
                            </span>
                          ))}
                        {Object.keys(res.tags || {}).length === 0 && (
                          <span className="text-slate-600">—</span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
