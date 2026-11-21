import { CheckCircle2, Circle, Loader2, Radio, XCircle } from 'lucide-react';
import type { ProgressEvent } from '../types';

interface ProgressTrackerProps {
  analysisId: string | null;
  resourceGroup: string;
  events: ProgressEvent[];
  wsConnected: boolean;
  isRunning: boolean;
}

const PIPELINE_STEPS = [
  { step: 1, stage: 'fetching_resource_groups', label: 'Fetching resource groups...' },
  { step: 2, stage: 'scanning_resources', label: 'Scanning AWS resources' },
  { step: 3, stage: 'analyzing_costs', label: 'Analyzing costs with AI...' },
  { step: 4, stage: 'storing_results', label: 'Storing results...' },
  { step: 5, stage: 'completed', label: 'Analysis complete' },
];

export default function ProgressTracker({
  analysisId,
  resourceGroup,
  events,
  wsConnected,
  isRunning,
}: ProgressTrackerProps) {
  const latest = events.length > 0 ? events[events.length - 1] : null;
  const isFailed = latest?.status === 'failed';
  const isCompleted = latest?.status === 'completed';
  const currentStep = latest?.step ?? (isRunning ? 1 : 0);
  const progressPct = latest?.progress ?? (isRunning ? 8 : 0);

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-6 shadow-xl">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-100">Live Analysis Progress</h2>
          <p className="mt-0.5 text-xs text-slate-400">
            Target:{' '}
            <span className="font-mono text-amber-300">{resourceGroup || 'Selected group'}</span>
            {analysisId && (
              <>
                {' '}
                · ID: <span className="font-mono text-slate-500">{analysisId.slice(0, 8)}</span>
              </>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
              isFailed
                ? 'border-rose-500/30 bg-rose-500/10 text-rose-300'
                : isCompleted
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                  : wsConnected
                    ? 'border-amber-500/30 bg-amber-500/10 text-amber-300'
                    : 'border-slate-700 bg-slate-800 text-slate-400'
            }`}
          >
            <Radio className={`h-3 w-3 ${isRunning && wsConnected ? 'animate-pulse' : ''}`} />
            {isFailed
              ? 'Failed'
              : isCompleted
                ? 'Completed'
                : wsConnected
                  ? 'WebSocket Live'
                  : 'Connecting...'}
          </span>
          <span className="font-mono text-sm font-semibold text-slate-200">{progressPct}%</span>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="mt-4 h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full transition-all duration-500 ease-out ${
            isFailed
              ? 'bg-rose-500'
              : isCompleted
                ? 'bg-emerald-500'
                : 'bg-gradient-to-r from-amber-500 via-orange-500 to-amber-400'
          }`}
          style={{ width: `${Math.min(100, Math.max(0, progressPct))}%` }}
        />
      </div>

      {/* 5-Stage Animated Step List */}
      <ol className="mt-6 grid gap-3 sm:grid-cols-5">
        {PIPELINE_STEPS.map((item) => {
          const matchingEvent = [...events].reverse().find((e) => e.stage === item.stage);
          const done =
            isCompleted ||
            (isFailed && item.step < currentStep) ||
            (!isFailed && currentStep > item.step);
          const active = !isCompleted && !isFailed && currentStep === item.step && isRunning;
          const failedHere = isFailed && currentStep === item.step;

          const displayLabel =
            matchingEvent?.message ||
            (item.step === 2 && resourceGroup
              ? `Scanning resources in ${resourceGroup}...`
              : item.label);

          return (
            <li
              key={item.step}
              className={`flex flex-col justify-between rounded-xl border p-3 transition-all duration-300 ${
                failedHere
                  ? 'border-red-500/40 bg-red-500/10 ring-1 ring-red-500/30'
                  : done
                    ? 'border-emerald-500/30 bg-emerald-500/5'
                    : active
                      ? 'border-amber-500/50 bg-amber-500/10 ring-1 ring-amber-400/30 shadow-lg shadow-amber-500/10'
                      : 'border-slate-800/80 bg-slate-950/50 opacity-75'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-[11px] font-medium text-slate-500">
                  STEP {item.step}/5
                </span>
                {failedHere ? (
                  <XCircle className="h-4 w-4 text-rose-400" />
                ) : done ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                ) : active ? (
                  <Loader2 className="h-4 w-4 animate-spin text-amber-400" />
                ) : (
                  <Circle className="h-4 w-4 text-slate-700" />
                )}
              </div>
              <p
                className={`mt-2 text-xs font-medium leading-snug ${
                  failedHere
                    ? 'text-rose-200'
                    : done
                      ? 'text-emerald-200'
                      : active
                        ? 'text-amber-200'
                        : 'text-slate-500'
                }`}
              >
                {displayLabel}
              </p>
            </li>
          );
        })}
      </ol>

      {/* Live Event Stream Log */}
      {events.length > 0 && (
        <div className="mt-5 rounded-xl border border-slate-800/90 bg-slate-950/80 p-3.5">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            WebSocket Event Log
          </div>
          <ul className="space-y-1.5 font-mono text-xs">
            {events.map((ev, idx) => (
              <li key={`${ev.timestamp}-${idx}`} className="flex items-start gap-2.5">
                <span className="shrink-0 text-slate-600">
                  {new Date(ev.timestamp).toLocaleTimeString()}
                </span>
                <span
                  className={
                    ev.status === 'failed'
                      ? 'text-rose-400'
                      : ev.status === 'completed'
                        ? 'text-emerald-400'
                        : 'text-slate-300'
                  }
                >
                  {ev.message}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
