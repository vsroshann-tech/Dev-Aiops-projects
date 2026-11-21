import type {
  AnalyzeResponse,
  AuthResponse,
  HistoryItem,
  HistoryResponse,
  ResourceGroupsResponse,
  UserPublic,
} from '../types';

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
).replace(/\/+$/, '');

export const WS_BASE_URL = API_BASE_URL.replace(/^http/i, 'ws');

const TOKEN_STORAGE_KEY = 'cost_detective_jwt';
const USER_STORAGE_KEY = 'cost_detective_user';

export class ApiError extends Error {
  status: number;
  code?: string;
  hint?: string;
  details?: string;

  constructor(
    message: string,
    status: number,
    options?: { code?: string; hint?: string; details?: string }
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = options?.code;
    this.hint = options?.hint;
    this.details = options?.details;
  }
}

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function getStoredUser(): UserPublic | null {
  try {
    const raw = localStorage.getItem(USER_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as UserPublic;
  } catch {
    return null;
  }
}

export function saveAuthSession(token: string, user: UserPublic): void {
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
  localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(user));
}

export function clearAuthSession(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  localStorage.removeItem(USER_STORAGE_KEY);
}

interface BackendErrorShape {
  error?: {
    code?: string;
    message?: string;
    hint?: string;
    details?: string;
  };
  detail?: string | Array<{ msg?: string; loc?: Array<string | number> }>;
}

async function parseErrorResponse(res: Response): Promise<ApiError> {
  let body: BackendErrorShape | null = null;
  try {
    body = (await res.json()) as BackendErrorShape;
  } catch {
    // Non-JSON response
  }

  if (body?.error?.message) {
    return new ApiError(body.error.message, res.status, {
      code: body.error.code,
      hint: body.error.hint,
      details: body.error.details,
    });
  }

  if (typeof body?.detail === 'string') {
    return new ApiError(body.detail, res.status);
  }

  if (Array.isArray(body?.detail) && body.detail.length > 0) {
    const first = body.detail[0];
    const msg = first?.msg || 'Request validation failed.';
    return new ApiError(msg, res.status, { code: 'VALIDATION_ERROR' });
  }

  return new ApiError(`Request failed with HTTP ${res.status}`, res.status);
}

const JWT_AUTH_ERROR_CODES = new Set([
  'NOT_AUTHENTICATED',
  'TOKEN_EXPIRED',
  'INVALID_TOKEN',
  'USER_NOT_FOUND',
]);

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: { skipAuthRedirect?: boolean } = {}
): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(init.headers);

  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json');
  }
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers,
    });
  } catch {
    throw new ApiError(
      'Unable to reach the backend server. Ensure FastAPI is running on http://localhost:8000.',
      0,
      {
        code: 'NETWORK_ERROR',
        hint: 'Start the backend with: uvicorn main:app --reload --port 8000',
      }
    );
  }

  if (!res.ok) {
    const err = await parseErrorResponse(res);
    // Only log out on JWT/session 401s — NOT on AWS_AUTH_ERROR or OPENAI_AUTH_ERROR
    const isJwtFailure = !err.code || JWT_AUTH_ERROR_CODES.has(err.code);
    if (res.status === 401 && isJwtFailure && !options.skipAuthRedirect) {
      clearAuthSession();
      window.dispatchEvent(new CustomEvent('auth:unauthorized'));
    }
    throw err;
  }

  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Auth API
// ---------------------------------------------------------------------------

export function signupUser(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>(
    '/api/auth/signup',
    {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    },
    { skipAuthRedirect: true }
  );
}

export function loginUser(email: string, password: string): Promise<AuthResponse> {
  return request<AuthResponse>(
    '/api/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    },
    { skipAuthRedirect: true }
  );
}

export function fetchCurrentUser(): Promise<UserPublic> {
  return request<UserPublic>('/api/auth/me');
}

// ---------------------------------------------------------------------------
// AWS Resource Groups, Analysis & History API
// ---------------------------------------------------------------------------

export function fetchResourceGroups(
  region?: string,
  roleArn?: string,
  externalId?: string
): Promise<ResourceGroupsResponse> {
  const params = new URLSearchParams();
  if (region && region.trim()) {
    params.set('region', region.trim());
  }
  if (roleArn && roleArn.trim()) {
    params.set('role_arn', roleArn.trim());
  }
  if (externalId && externalId.trim()) {
    params.set('external_id', externalId.trim());
  }
  const qs = params.toString();
  return request<ResourceGroupsResponse>(`/api/resource-groups${qs ? `?${qs}` : ''}`);
}

export interface RunAnalysisParams {
  resource_group: string;
  analysis_id: string;
  region?: string;
  include_costs?: boolean;
  role_arn?: string;
  external_id?: string;
}

export function runCostAnalysis(params: RunAnalysisParams): Promise<AnalyzeResponse> {
  const payload: Record<string, unknown> = {
    resource_group: params.resource_group,
    analysis_id: params.analysis_id,
    include_costs: params.include_costs ?? true,
  };
  if (params.region && params.region.trim()) {
    payload.region = params.region.trim();
  }
  if (params.role_arn && params.role_arn.trim()) {
    payload.role_arn = params.role_arn.trim();
  }
  if (params.external_id && params.external_id.trim()) {
    payload.external_id = params.external_id.trim();
  }

  return request<AnalyzeResponse>('/api/analyze', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function fetchHistory(limit = 50, offset = 0): Promise<HistoryResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return request<HistoryResponse>(`/api/history?${params.toString()}`);
}

export function fetchHistoryItem(analysisId: string): Promise<HistoryItem> {
  return request<HistoryItem>(`/api/history/${encodeURIComponent(analysisId)}`);
}

export function buildProgressWebSocketUrl(analysisId: string): string {
  const token = getStoredToken();
  const base = `${WS_BASE_URL}/ws/progress/${encodeURIComponent(analysisId)}`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}
