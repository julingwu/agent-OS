import React, { Component } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }
  componentDidCatch(error, errorInfo) {
    console.error("AgentOS UI ErrorBoundary caught error:", error, errorInfo);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: "48px 32px",
          color: "#e2e8f0",
          background: "#080b14",
          height: "100vh",
          boxSizing: "border-box",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif"
        }}>
          <div style={{
            maxWidth: "560px",
            width: "100%",
            background: "rgba(15, 23, 42, 0.9)",
            border: "1px solid rgba(56, 232, 255, 0.3)",
            borderRadius: "14px",
            padding: "28px",
            boxShadow: "0 0 35px rgba(56, 232, 255, 0.1)"
          }}>
            <h2 style={{ margin: "0 0 12px 0", color: "#38e8ff", fontSize: "20px" }}>⚠️ AgentOS 界面遇到异常</h2>
            <p style={{ color: "#94a3b8", fontSize: "14px", lineHeight: "1.6", margin: "0 0 20px 0" }}>
              {String(this.state.error?.message || this.state.error || "遇到未预期的运行时错误，已安全隔离")}
            </p>
            <div style={{ display: "flex", gap: "12px" }}>
              <button
                style={{
                  padding: "8px 18px",
                  background: "linear-gradient(135deg, #0284c7, #2563eb)",
                  color: "#fff",
                  border: "none",
                  borderRadius: "8px",
                  cursor: "pointer",
                  fontWeight: "bold",
                  fontSize: "13px"
                }}
                onClick={() => {
                  this.setState({ hasError: false, error: null });
                  window.location.href = window.location.origin + "/ui/?_t=" + Date.now();
                }}
              >
                刷新恢复
              </button>
              <button
                style={{
                  padding: "8px 18px",
                  background: "rgba(255, 255, 255, 0.08)",
                  color: "#cbd5e1",
                  border: "1px solid rgba(255, 255, 255, 0.15)",
                  borderRadius: "8px",
                  cursor: "pointer",
                  fontSize: "13px"
                }}
                onClick={() => this.setState({ hasError: false, error: null })}
              >
                尝试继续
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("unhandledrejection", (ev) => {
    console.warn("Global unhandled rejection caught:", ev.reason);
    ev.preventDefault();
  });
}

createRoot(document.getElementById("root")).render(
  <ErrorBoundary>
    <App />
  </ErrorBoundary>
);
