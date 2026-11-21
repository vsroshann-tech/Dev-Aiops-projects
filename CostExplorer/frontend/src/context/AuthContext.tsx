import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  clearAuthSession,
  fetchCurrentUser,
  getStoredToken,
  getStoredUser,
  loginUser,
  saveAuthSession,
  signupUser,
} from '../lib/api';
import type { UserPublic } from '../types';

interface AuthContextValue {
  user: UserPublic | null;
  token: string | null;
  isAuthenticated: boolean;
  isCheckingAuth: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [user, setUser] = useState<UserPublic | null>(() => getStoredUser());
  const [isCheckingAuth, setIsCheckingAuth] = useState<boolean>(() => Boolean(getStoredToken()));
  const skipNextVerifyRef = useRef<boolean>(false);

  const logout = useCallback(() => {
    clearAuthSession();
    setToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    const onUnauthorized = () => {
      setToken(null);
      setUser(null);
    };
    window.addEventListener('auth:unauthorized', onUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', onUnauthorized);
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setIsCheckingAuth(false);
      return;
    }

    if (skipNextVerifyRef.current) {
      skipNextVerifyRef.current = false;
      setIsCheckingAuth(false);
      return;
    }

    fetchCurrentUser()
      .then((profile) => {
        if (!cancelled) {
          setUser(profile);
          saveAuthSession(token, profile);
        }
      })
      .catch(() => {
        if (!cancelled) {
          clearAuthSession();
          setToken(null);
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsCheckingAuth(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  const login = useCallback(async (email: string, password: string) => {
    const data = await loginUser(email, password);
    skipNextVerifyRef.current = true;
    saveAuthSession(data.access_token, data.user);
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const signup = useCallback(async (email: string, password: string) => {
    const data = await signupUser(email, password);
    skipNextVerifyRef.current = true;
    saveAuthSession(data.access_token, data.user);
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      isAuthenticated: Boolean(token),
      isCheckingAuth,
      login,
      signup,
      logout,
    }),
    [user, token, isCheckingAuth, login, signup, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside an AuthProvider');
  }
  return ctx;
}
