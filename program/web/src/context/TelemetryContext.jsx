import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from "react";

const TelemetryContext = createContext({
  connected: false,
  latestEvent: null,
  events: [],
  send: () => {},
  subscribe: () => () => {},
});

export function TelemetryProvider({ children }) {
  const [connected, setConnected] = useState(false);
  const [latestEvent, setLatestEvent] = useState(null);
  const [events, setEvents] = useState([]);
  const socketRef = useRef(null);
  const subscribersRef = useRef(new Map());

  const subscribe = useCallback((eventType, callback) => {
    if (!subscribersRef.current.has(eventType)) {
      subscribersRef.current.set(eventType, new Set());
    }
    subscribersRef.current.get(eventType).add(callback);
    return () => {
      const set = subscribersRef.current.get(eventType);
      if (set) {
        set.delete(callback);
        if (set.size === 0) {
          subscribersRef.current.delete(eventType);
        }
      }
    };
  }, []);

  const send = useCallback((payload) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(typeof payload === "string" ? payload : JSON.stringify(payload));
    }
  }, []);

  useEffect(() => {
    let unmounted = false;
    let reconnectTimer = null;

    const connect = () => {
      if (unmounted) return;
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = window.location.host || "127.0.0.1:8077";
      const wsUrl = `${protocol}//${host}/ws/metacorp`;

      try {
        const ws = new WebSocket(wsUrl);
        socketRef.current = ws;

        ws.onopen = () => {
          if (unmounted) return;
          setConnected(true);
          try {
            ws.send(JSON.stringify({ action: "ping" }));
          } catch { /* ignore */ }
        };

        ws.onmessage = (event) => {
          if (unmounted) return;
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === "PONG") return;

            setLatestEvent(msg);
            setEvents((prev) => [...prev.slice(-49), msg]);

            // Notify specific subscribers
            const callbacks = subscribersRef.current.get(msg.type);
            if (callbacks) {
              callbacks.forEach((cb) => {
                try { cb(msg); } catch (e) { console.error("Telemetry subscriber error:", e); }
              });
            }
            // Notify global wildcard subscribers
            const allCallbacks = subscribersRef.current.get("*");
            if (allCallbacks) {
              allCallbacks.forEach((cb) => {
                try { cb(msg); } catch (e) { console.error("Telemetry global subscriber error:", e); }
              });
            }
          } catch {
            // raw text or unparseable
          }
        };

        ws.onerror = () => {
          if (unmounted) return;
          setConnected(false);
        };

        ws.onclose = () => {
          if (unmounted) return;
          setConnected(false);
          reconnectTimer = setTimeout(connect, 3000);
        };
      } catch (err) {
        if (!unmounted) {
          setConnected(false);
          reconnectTimer = setTimeout(connect, 5000);
        }
      }
    };

    connect();

    return () => {
      unmounted = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, []);

  const value = {
    connected,
    latestEvent,
    events,
    send,
    subscribe,
  };

  return <TelemetryContext.Provider value={value}>{children}</TelemetryContext.Provider>;
}

export function useTelemetry() {
  return useContext(TelemetryContext);
}

export default TelemetryContext;
