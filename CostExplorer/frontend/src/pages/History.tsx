import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Calendar,
  History as HistoryIcon,
  Layers,
  Loader2,
  RefreshCw,
  Search,
} from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError, fetchHistory } from '../lib/api';
import type { AnalyzeResponse, HistoryItem } from '../types';

export default function History() {
  const navigate = useNavigate();

  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);
  const [search, setSearch] = useState<string>('');

  const loadHistory = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchHistory();
      setItems(data.analyses);
    } catch (err) {
      if (err instanceof ApiError) {
        setError({ message: err.message, hint: err.hint });
      } else {
        setError({ message: 'Failed to load analysis history.' });
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (item) =>
        item.resource_group.toLowerCase().includes(q) ||
        item.estimated_savings.toLowerCase().includes(q) ||
        item.status.toLowerCase().includes(q)
    );
  }, [items, search]);

  const openReport = (item: HistoryItem) => {
    const fullResult = item.analysis_result as AnalyzeResponse | undefined;
    const hasFullReport = Boolean(fullResult && fullResult.analysis);
    navigate(`/report/${encodeURIComponent(item.id)}`, {
      state: hasFullReport ? { report: fullResult } : undefined,
    });
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-1 text-xs font-medium text-slate-300">
            <HistoryIcon className="h-3.5 w-3.5 text-amber-400" />
            <span>Amazon RDS PostgreSQL History</span>
          </div>
          <h1 className="mt-3 text-2xl font-bold tracking-tight text-slate-100 sm:text-3xl">
            Past Cost Analyses
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            Click any completed scan to view the full AI report, severity breakdown, and AWS CLI fix
            commands.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter by group..."
              className="w-48 rounded-xl border border-slate-800 bg-slate-900 py-2 pl-9 pr-3 text-xs text-slate-100 placeholder-slate-500 focus:border-amber-500/50 focus:outline-none sm:w-56"
            />
          </div>

          <button
            type="button"
            disabled={loading}
            onClick={() => void loadHistory()}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900 px-3.5 py-2 text-xs font-medium text-slate-300 transition-colors hover:border-slate-700 hover:text-slate-100 disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 text-sm text-rose-200">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-400" />
          <div>
            <p className="font-semibold">{error.message}</p>
            {error.hint && <p className="mt-1 text-xs text-rose-300/85">{error.hint}</p>}
          </div>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/50 py-16 text-slate-400">
          <Loader2 className="h-6 w-6 animate-spin text-amber-400" />
          <p className="text-sm">Loading past analyses...</p>
        </div>
      )}

      {/* Empty State */}
      {!loading && !error && filteredItems.length === 0 && (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-12 text-center">
          <Layers className="mx-auto h-10 w-10 text-slate-600" />
          <h2 className="mt-4 text-base font-semibold text-slate-200">
            {items.length === 0 ? 'No analyses yet' : 'No matching analyses'}
          </h2>
          <p className="mt-1 text-sm text-slate-400">
            {items.length === 0
              ? 'Run your first AWS resource group analysis from the Dashboard to see it saved here.'
              : 'Try adjusting your search query.'}
          </p>
          {items.length === 0 && (
            <Link
              to="/dashboard"
              className="mt-5 inline-flex items-center gap-2 rounded-xl bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400"
            >
              <span>Run First Analysis</span>
              <ArrowRight className="h-4 w-4" />
            </Link>
          )}
        </div>
      )}

      {/* History Table */}
      {!loading && !error && filteredItems.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70 shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-800 bg-slate-950/80 text-xs uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-5 py-3.5">AWS Resource / Tag Group</th>
                  <th className="px-5 py-3.5">Date</th>
                  <th className="px-5 py-3.5 text-center">Resources</th>
                  <th className="px-5 py-3.5 text-center">Issues Found</th>
                  <th className="px-5 py-3.5">Estimated Savings</th>
                  <th className="px-5 py-3.5 text-right">Report</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/80">
                {filteredItems.map((item) => (
                  <tr
                    key={item.id}
                    onClick={() => openReport(item)}
                    className="cursor-pointer transition-colors hover:bg-slate-800/50"
                  >
                    <td className="px-5 py-4">
                      <div className="font-semibold text-slate-100">{item.resource_group}</div>
                      <div className="mt-0.5 font-mono text-[11px] text-slate-500">
                        ID: {item.id.slice(0, 12)}
                      </div>
                    </td>
                    <td className="px-5 py-4 text-xs text-slate-300">
                      <div className="inline-flex items-center gap-1.5">
                        <Calendar className="h-3.5 w-3.5 text-slate-500" />
                        <span>
                          {item.created_at ? new Date(item.created_at).toLocaleString() : '—'}
                        </span>
                      </div>
                    </td>
                    <td className="px-5 py-4 text-center font-mono text-xs text-slate-300">
                      {item.resources_scanned}
                    </td>
                    <td className="px-5 py-4 text-center">
                      <span
                        className={`inline-flex rounded-full border px-2.5 py-0.5 font-mono text-xs font-semibold ${
                          item.issues_found > 0
                            ? 'border-amber-500/40 bg-amber-500/15 text-amber-300'
                            : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                        }`}
                      >
                        {item.issues_found}
                      </span>
                    </td>
                    <td className="px-5 py-4 font-mono text-xs font-semibold text-emerald-300">
                      {item.estimated_savings}
                    </td>
                    <td className="px-5 py-4 text-right">
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-400">
                        Open
                        <ArrowRight className="h-3.5 w-3.5" />
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
