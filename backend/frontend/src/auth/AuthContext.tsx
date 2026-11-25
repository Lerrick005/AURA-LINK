import React, { createContext, useContext, useState, ReactNode } from "react";

interface AuthState {
  token: string;
  organization_id: string;
}

interface AuthContextType {
  auth: AuthState | null;
  login: (token: string, organizationId: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState | null>(() => {
    try {
      const saved = localStorage.getItem("auth");
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });

  const login = (token: string, organizationId: string) => {
    const state = { token, organization_id: organizationId };
    localStorage.setItem("auth", JSON.stringify(state));
    setAuth(state);
  };

  const logout = () => {
    localStorage.removeItem("auth");
    setAuth(null);
  };

  return (
    <AuthContext.Provider value={{ auth, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}