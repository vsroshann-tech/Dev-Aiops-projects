import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Globe,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  Server,
  Sparkles,
  Tag,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import ProgressTracker from '../components/ProgressTracker';
import {
  ApiError,
  buildProgressWebSocketUrl,
  fetchResourceGroups,
  runCostAnalysis,
} from '../lib/api';
import type { AnalyzeResponse, ProgressEvent, ResourceGroupItem } from '../types';

function createAnalysisId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `run-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export default function Dashboard() {
  const navigate = useNavigate();

  const [groups, setGroups] = useState<ResourceGroupItem[]>([]);
  const [accountId, setAccountId] = useState<string>('');
  const [activeRegion, setActiveRegion] = useState<string>('');
  const [regionOverride, setRegionOverride] = useState<string>('');
  const [groupWarnings, setGroupWarnings] = useState<string[]>([]);

  const [selectedGroup, setSelectedGroup] = useState<string>('');
  const [customFilter, setCustomFilter] = useState<string>('');
  const [useCustomFilter, setUseCustomFilter] = useState<boolean>(false);
  const [roleArn, setRoleArn] = useState<string>('');
  const [externalId, setExternalId] = useState<string>('');

  const [loadingGroups, setLoadingGroups] = useState<boolean>(true);
  const [groupsError, setGroupsError] = useState<{ message: string; hint?: string } | null>(null);

  // Analysis & WebSocket state
  const [isAnalyzing, setIsAnalyzing] = useState<boolean>(false);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [wsConnected, setWsConnected] = useState<boolean>(false);
  const [analyzeError, setAnalyzeError] = useState<{ message: string; hint?: string } | null>(null);
  const [completedReport, setCompletedReport] = useState<AnalyzeResponse | null>(null);

  const wsRef = useRef<WebSocket | null>(null);

  const closeSocket = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.onopen = null;
      wsRef.current.onmessage = null;
      wsRef.current.onerror = null;
      wsRef.current.onclose = null;
      if (
        wsRef.current.readyState === WebSocket.OPEN ||
        wsRef.current.readyState === WebSocket.CONNECTING
      ) {
        wsRef.current.close();
      }
      wsRef.current = null;
    }
    setWsConnected(false);
  }, []);

  useEffect(() => {
    return () => {
      closeSocket();
    };
  }, [closeSocket]);

  const loadResourceGroups = useCallback(
    async (region?: string, arn?: string, extId?: string) => {
      setLoadingGroups(true);
      setGroupsError(null);
      try {
        const data = await fetchResourceGroups(region, arn, extId);
      setGroups(data.resource_groups);
      setAccountId(data.account_id);
      setActiveRegion(data.region);
      setGroupWarnings(data.warnings || []);

      if (data.resource_groups.length > 0) {
        setSelectedGroup((prev) => {
          const exists = data.resource_groups.some((g) => g.name === prev);
          return exists ? prev : data.resource_groups[0].name;
        });
      } else {
        setSelectedGroup('');
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setGroupsError({ message: err.message, hint: err.hint });
      } else {
        setGroupsError({ message: 'Failed to load AWS resource groups.' });
      }
    } finally {
      setLoadingGroups(false);
    }
  }, []);

  useEffect(() => {
    void loadResourceGroups();
  }, [loadResourceGroups]);

  const groupedOptions = useMemo(() => {
    return {
      fullAccount: groups.filter((g) => g.source === 'full_account'),
      formal: groups.filter((g) => g.source === 'aws_resource_groups'),
      stacks: groups.filter((g) => g.source === 'cloudformation_stack'),
      tags: groups.filter((g) => g.source === 'tag_filter'),
      other: groups.filter(
        (g) =>
          g.source !== 'full_account' &&
          g.source !== 'aws_resource_groups' &&
          g.source !== 'cloudformation_stack' &&
          g.source !== 'tag_filter'
      ),
    };
  }, [groups]);

  const targetGroup = (useCustomFilter ? customFilter : selectedGroup).trim();

  const handleRunAnalysis = async () => {
    if (!targetGroup || isAnalyzing) return;

    setAnalyzeError(null);
    setCompletedReport(null);
    setProgressEvents([]);
    setIsAnalyzing(true);
    closeSocket();

    const newAnalysisId = createAnalysisId();
    setAnalysisId(newAnalysisId);

    // Connect WebSocket before triggering POST /api/analyze
    const wsUrl = buildProgressWebSocketUrl(newAnalysisId);
    let activeRun = true;
    let reconnected = false;

    const attachSocket = (url: string): Promise<void> => {
      const socket = new WebSocket(url);
      wsRef.current = socket;

      return new Promise<void>((resolve) => {
        const timer = window.setTimeout(() => resolve(), 350);
        socket.onopen = () => {
          window.clearTimeout(timer);
          setWsConnected(true);
          resolve();
        };
        socket.onerror = () => {
          window.clearTimeout(timer);
          setWsConnected(false);
          resolve();
        };
        socket.onmessage = (event) => {
          try {
            const parsed = JSON.parse(event.data) as ProgressEvent;
            setProgressEvents((prev) => {
              const duplicate = prev.some(
                (e) =>
                  e.step === parsed.step &&
                  e.stage === parsed.stage &&
                  e.message === parsed.message
              );
              return duplicate ? prev : [...prev, parsed];
            });
          } catch {
            // Ignore malformed frames
          }
        };
        socket.onclose = () => {
          setWsConnected(false);
          if (activeRun && !reconnected) {
            reconnected = true;
            window.setTimeout(() => {
              if (activeRun) {
                void attachSocket(url);
              }
            }, 600);
          }
        };
      });
    };

    await attachSocket(wsUrl);

    try {
      const result = await runCostAnalysis({
        resource_group: targetGroup,
        analysis_id: newAnalysisId,
        region: regionOverride.trim() || undefined,
        include_costs: true,
        role_arn: roleArn.trim() || undefined,
        external_id: externalId.trim() || undefined,
      });

      activeRun = false;
      setCompletedReport(result);
      setProgressEvents((prev) => {
        if (prev.some((e) => e.status === 'completed')) return prev;
        return [
          ...prev,
          {
            analysis_id: result.analysis_id,
            step: 5,
            total_steps: 5,
            stage: 'completed',
            message: 'Analysis complete',
            progress: 100,
            status: 'completed',
            timestamp: new Date().toISOString(),
          },
        ];
      });

      // Brief pause so the user sees "Analysis complete" at 100% before transitioning
      window.setTimeout(() => {
        closeSocket();
        navigate(`/report/${encodeURIComponent(result.analysis_id)}`, {
          state: { report: result },
        });
      }, 650);
    } catch (err) {
      activeRun = false;
      if (err instanceof ApiError) {
        setAnalyzeError({ message: err.message, hint: err.hint });
      } else {
        setAnalyzeError({ message: 'Analysis failed unexpectedly. Please try again.' });
      }
      window.setTimeout(() => closeSocket(), 400);
    } finally {
      activeRun = false;
      setIsAnalyzing(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      {/* Page Hero */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-300">
            <Sparkles className="h-3.5 w-3.5" />
            <span>Powered by AWS CLI + GPT-4o</span>
          </div>
          <h1 className="mt-3 text-2xl font-bold tracking-tight text-slate-100 sm:text-3xl">
            AWS Cost Optimization Scanner
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-slate-400">
            Select an AWS Resource Group, CloudFormation stack, or tag grouping to uncover idle
            resources, over-provisioning, legacy SKUs, and ready-to-run AWS CLI fixes.
          </p>
        </div>

        {/* Account / Region Meta Pills */}
        {(accountId || activeRegion) && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {accountId && (
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-3 py-1.5 font-mono text-slate-300">
                <Server className="h-3.5 w-3.5 text-amber-400" />
                AWS {accountId}
              </span>
            )}
            {activeRegion && (
              <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-800 bg-slate-900 px-3 py-1.5 font-mono text-slate-300">
                <Globe className="h-3.5 w-3.5 text-sky-400" />
                {activeRegion}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Main Scanner Configuration Card */}
      <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800/80 pb-4">
          <div className="flex items-center gap-2.5">
            <Layers className="h-5 w-5 text-amber-400" />
            <h2 className="text-base font-semibold text-slate-100">
              Select Target Resource Group
            </h2>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setUseCustomFilter((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-amber-500/40 hover:text-amber-300"
            >
              <Tag className="h-3.5 w-3.5" />
              {useCustomFilter ? 'Use Discovered Dropdown' : 'Use Custom Filter'}
            </button>

            <button
              type="button"
              disabled={loadingGroups || isAnalyzing}
              onClick={() =>
                void loadResourceGroups(
                  regionOverride.trim() || undefined,
                  roleArn.trim() || undefined,
                  externalId.trim() || undefined
                )
              }
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:border-slate-600 hover:text-slate-100 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loadingGroups ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Group Loading Error */}
        {groupsError && (
          <div className="mt-4 flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />
            <div>
              <p className="font-semibold">{groupsError.message}</p>
              {groupsError.hint && <p className="mt-1 text-xs text-rose-300/80">{groupsError.hint}</p>}
            </div>
          </div>
        )}

        {/* Group Discovery Warnings */}
        {groupWarnings.length > 0 && (
          <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3.5 text-xs text-amber-200">
            <p className="font-semibold">AWS Scanner Notices:</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-amber-200/85">
              {groupWarnings.map((w, idx) => (
                <li key={idx}>{w}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Form Controls */}
        <div className="mt-5 grid gap-4 md:grid-cols-12">
          <div className="md:col-span-6">
            <label
              htmlFor="resource-group-input"
              className="mb-1.5 block text-xs font-medium text-slate-300"
            >
              {useCustomFilter
                ? 'Custom Group / Tag / Stack / Service Filter'
                : 'AWS Resource Group or Tag Grouping'}
            </label>

            {useCustomFilter ? (
              <input
                id="resource-group-input"
                type="text"
                value={customFilter}
                onChange={(e) => setCustomFilter(e.target.value)}
                disabled={isAnalyzing}
                placeholder="e.g. prod-web-stack, tag:Environment=prod, stack:my-app, service:ec2"
                className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:border-amber-500/60 focus:outline-none focus:ring-2 focus:ring-amber-500/20 disabled:opacity-60"
              />
            ) : (
              <select
                id="resource-group-input"
                value={selectedGroup}
                onChange={(e) => setSelectedGroup(e.target.value)}
                disabled={loadingGroups || isAnalyzing || groups.length === 0}
                className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2.5 text-sm text-slate-100 focus:border-amber-500/60 focus:outline-none focus:ring-2 focus:ring-amber-500/20 disabled:opacity-60"
              >
                {loadingGroups && <option value="">Loading AWS resource groups...</option>}
                {!loadingGroups && groups.length === 0 && (
                  <option value="">No resource groups found — switch to Custom Filter</option>
                )}

                {groupedOptions.fullAccount.length > 0 && (
                  <optgroup label="Full Account Scan">
                    {groupedOptions.fullAccount.map((g) => (
                      <option key={g.name} value={g.name}>
                        {g.name}
                        {g.description ? ` — ${g.description}` : ''}
                      </option>
                    ))}
                  </optgroup>
                )}

                {groupedOptions.formal.length > 0 && (
                  <optgroup label="AWS Resource Groups">
                    {groupedOptions.formal.map((g) => (
                      <option key={g.name} value={g.name}>
                        {g.name}
                        {g.description ? ` — ${g.description}` : ''}
                      </option>
                    ))}
                  </optgroup>
                )}

                {groupedOptions.stacks.length > 0 && (
                  <optgroup label="CloudFormation Stacks">
                    {groupedOptions.stacks.map((g) => (
                      <option key={g.name} value={g.name}>
                        {g.name}
                        {typeof g.resource_count === 'number'
                          ? ` (${g.resource_count} resources)`
                          : ''}
                      </option>
                    ))}
                  </optgroup>
                )}

                {groupedOptions.tags.length > 0 && (
                  <optgroup label="Tag-Based Groups">
                    {groupedOptions.tags.map((g) => (
                      <option key={g.name} value={g.name}>
                        {g.name}
                        {typeof g.resource_count === 'number'
                          ? ` (${g.resource_count} resources)`
                          : ''}
                      </option>
                    ))}
                  </optgroup>
                )}

                {groupedOptions.other.length > 0 && (
                  <optgroup label="Other Groups">
                    {groupedOptions.other.map((g) => (
                      <option key={g.name} value={g.name}>
                        {g.name}
                      </option>
                    ))}
                  </optgroup>
                )}
              </select>
            )}
          </div>

          <div className="md:col-span-3">
            <label
              htmlFor="region-override"
              className="mb-1.5 block text-xs font-medium text-slate-300"
            >
              AWS Region <span className="text-slate-500">(optional)</span>
            </label>
            <input
              id="region-override"
              type="text"
              value={regionOverride}
              onChange={(e) => setRegionOverride(e.target.value)}
              disabled={isAnalyzing}
              placeholder={activeRegion || 'us-east-1'}
              className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:border-amber-500/60 focus:outline-none focus:ring-2 focus:ring-amber-500/20 disabled:opacity-60"
            />
          </div>

          <div className="flex items-end md:col-span-3">
            <button
              type="button"
              onClick={() => void handleRunAnalysis()}
              disabled={!targetGroup || isAnalyzing}
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-amber-500 to-orange-500 px-5 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-amber-500/20 transition-all hover:from-amber-400 hover:to-orange-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isAnalyzing ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Analyzing...</span>
                </>
              ) : (
                <>
                  <Play className="h-4 w-4 fill-current" />
                  <span>Run Analysis</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Optional IAM Role Assumption (sts:AssumeRole) */}
        <div className="mt-4 grid gap-4 border-t border-slate-800/80 pt-4 md:grid-cols-12">
          <div className="md:col-span-6">
            <label
              htmlFor="role-arn-input"
              className="mb-1.5 block text-xs font-medium text-slate-300"
            >
              IAM Role ARN <span className="text-slate-500">(sts:AssumeRole)</span>
            </label>
            <input
              id="role-arn-input"
              type="text"
              value={roleArn}
              onChange={(e) => setRoleArn(e.target.value)}
              disabled={isAnalyzing}
              placeholder="arn:aws:iam::956970629130:role/CostDetectiveReadOnlyRole"
              className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2 font-mono text-xs text-slate-100 placeholder-slate-500 focus:border-amber-500/60 focus:outline-none focus:ring-2 focus:ring-amber-500/20 disabled:opacity-60"
            />
          </div>

          <div className="md:col-span-3">
            <label
              htmlFor="external-id-input"
              className="mb-1.5 block text-xs font-medium text-slate-300"
            >
              External ID <span className="text-slate-500">(optional)</span>
            </label>
            <input
              id="external-id-input"
              type="text"
              value={externalId}
              onChange={(e) => setExternalId(e.target.value)}
              disabled={isAnalyzing}
              placeholder="optional-external-id"
              className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2 font-mono text-xs text-slate-100 placeholder-slate-500 focus:border-amber-500/60 focus:outline-none focus:ring-2 focus:ring-amber-500/20 disabled:opacity-60"
            />
          </div>

          <div className="flex items-end md:col-span-3">
            <button
              type="button"
              disabled={loadingGroups || isAnalyzing || !roleArn.trim()}
              onClick={() =>
                void loadResourceGroups(
                  regionOverride.trim() || undefined,
                  roleArn.trim() || undefined,
                  externalId.trim() || undefined
                )
              }
              className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-xs font-semibold text-amber-300 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loadingGroups ? 'animate-spin' : ''}`} />
              <span>Connect Role &amp; Load Account</span>
            </button>
          </div>
        </div>

        {/* Helpful Filter Syntax Hints */}
        <p className="mt-3 text-xs text-slate-500">
          Supports formal AWS Resource Groups,{' '}
          <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">tag:Key=Value</code>,{' '}
          <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">
            stack:&lt;name&gt;
          </code>
          , and{' '}
          <code className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">service:ec2</code>.
        </p>
      </section>

      {/* Analyze Error Banner */}
      {analyzeError && (
        <div className="flex items-start gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 text-sm text-rose-200">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />
          <div>
            <p className="font-semibold">{analyzeError.message}</p>
            {analyzeError.hint && (
              <p className="mt-1 text-xs text-rose-300/85">{analyzeError.hint}</p>
            )}
          </div>
        </div>
      )}

      {/* Live WebSocket Progress Section */}
      {(isAnalyzing || progressEvents.length > 0) && (
        <ProgressTracker
          analysisId={analysisId}
          resourceGroup={targetGroup}
          events={progressEvents}
          wsConnected={wsConnected}
          isRunning={isAnalyzing}
        />
      )}

      {/* Quick Link Banner if report finished */}
      {completedReport && !isAnalyzing && (
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-5">
          <div>
            <p className="text-sm font-semibold text-emerald-200">
              Analysis complete — {completedReport.analysis.issue_count} issues found
            </p>
            <p className="mt-0.5 text-xs text-emerald-300/80">
              Estimated savings: {completedReport.analysis.estimated_savings.display}
            </p>
          </div>
          <button
            type="button"
            onClick={() =>
              navigate(`/report/${encodeURIComponent(completedReport.analysis_id)}`, {
                state: { report: completedReport },
              })
            }
            className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-500 px-4 py-2 text-xs font-semibold text-slate-950 transition-colors hover:bg-emerald-400"
          >
            <span>Open Full Report</span>
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}
