/* Required Notice: Copyright (c) 2026 赛博老爸 (PolyForm Noncommercial License 1.0.0) — Adapted & Wired for AgentOS */

import React, { useEffect, useRef, useImperativeHandle, forwardRef } from "react";
import { HQ_TEMPLATE_HTML, mountHQ } from "./hq_engine_core.js";

const CyberHQEngine = forwardRef(function CyberHQEngine({
  wsConnected = false,
  totalTokens = 0,
  pendingCount = 0,
  projectList = [],
  activeProjectId = "",
  onCreateTask = () => {},
  onSwitchStudio = () => {},
  onOpenExchange = () => {},
  onNavigateTab = () => {},
}, ref) {
  const wrapperRef = useRef(null);
  const hqInstRef = useRef(null);

  // Expose methods to parent component
  useImperativeHandle(ref, () => ({
    pushBackendEvent: (evt) => {
      hqInstRef.current?.pushBackendEvent(evt);
    },
    syncBackendData: (data) => {
      hqInstRef.current?.syncBackendData(data);
    },
    openTaskModal: () => {
      hqInstRef.current?.openTaskModal();
    },
  }));

  // Mount lifecycle: 100% immune to React StrictMode double-invoke!
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;

    const mountEl = document.createElement("div");
    mountEl.style.cssText = "width:100%;height:100%;position:relative;overflow:hidden;";
    wrapper.appendChild(mountEl);

    const sr = mountEl.attachShadow({ mode: "open" });
    sr.innerHTML = HQ_TEMPLATE_HTML;

    const bridge = {
      onCreateTask: (taskData) => {
        if (onCreateTask) onCreateTask(taskData);
      },
      onSwitchStudio: () => {
        if (onSwitchStudio) onSwitchStudio();
      },
      onOpenExchange: () => {
        if (onOpenExchange) onOpenExchange();
      },
      onNavigateTab: (tabKey) => {
        if (onNavigateTab) onNavigateTab(tabKey);
      },
    };

    const inst = mountHQ(mountEl, sr, bridge);
    hqInstRef.current = inst;

    // Initial sync
    inst.syncBackendData({
      wsConnected,
      totalTokens,
      pendingCount,
      projects: projectList,
    });

    return () => {
      inst.destroy();
      hqInstRef.current = null;
      if (mountEl.parentNode) {
        mountEl.parentNode.removeChild(mountEl);
      }
    };
  }, []);

  // Reactive data sync
  useEffect(() => {
    if (hqInstRef.current) {
      hqInstRef.current.syncBackendData({
        wsConnected,
        totalTokens,
        pendingCount,
        projects: projectList,
      });
    }
  }, [wsConnected, totalTokens, pendingCount, projectList]);

  return (
    <div
      ref={wrapperRef}
      style={{
        width: "100%",
        height: "calc(100vh - 56px)",
        minHeight: "680px",
        overflow: "hidden",
        position: "relative",
        background: "#070b14",
      }}
    />
  );
});

export default CyberHQEngine;
