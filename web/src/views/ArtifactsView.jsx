import React, { useState, useEffect, useCallback, useRef } from "react";
import api from "../api.js";
import { cls } from "./common.jsx";
import { ResourceSection } from "./UsageFinOpsView.jsx";

export const PREVIEWABLE = [
  ".html", ".htm", ".svg", ".png", ".jpg", ".jpeg", ".md", ".markdown",
  ".txt", ".json", ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".sh",
  ".bat", ".sql", ".yaml", ".yml", ".xml", ".csv", ".log", ".env"
];

export function PreviewModal({ file, onClose }) {
  const frame = useRef(null);
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    setSource(""); setError("");
    api.previewTicket(file.id).then(result => {
      if (alive) setSource(api.previewUrl(file.id, result.ticket));
    }).catch(err => { if (alive) setError(err.message || "预览暂时不可用，请重试。"); });
    return () => { alive = false; };
  }, [file.id, attempt]);

  useEffect(() => {
    const receive = event => {
      if (frame.current && event.source === frame.current.contentWindow && event.data?.kind === "agent-os-preview-close") onClose();
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [onClose]);

  const name = file.name || (file.file_path || "").split(/[\\/]/).pop();
  const ext = ("." + name.split(".").pop()).toLowerCase();
  const isImage = [".png", ".jpg", ".jpeg", ".svg"].includes(ext);

  return (
    <div className="mask" onClick={onClose}>
      <div className="modal preview" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <div>
            <div className="who">{name}</div>
            <div className="sub">{file.task_id ? `来自 ${file.task_id}` : "沙箱产物"} · {file.bytes || file.size || 0} B</div>
          </div>
          <div className="spacer" />
          <a className="btn ghost" href={api.fileUrl(file.id)} target="_blank" rel="noreferrer">下载</a>
          <button className="icon-btn" onClick={onClose}>×</button>
        </div>
        {source ? (
          <iframe
            ref={frame}
            className="preview-frame"
            title={`文件预览：${name}`}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            src={source}
          />
        ) : error ? (
          <div className="empty" role="alert">
            {error}
            <div className="actions">
              <button className="btn" onClick={() => setAttempt(value => value + 1)}>重新加载预览</button>
            </div>
          </div>
        ) : (
          <div className="empty" role="status">正在准备预览…</div>
        )}
        <div className="hint">安全预览会屏蔽外部资源，完整内容可下载查看。</div>
        {source && isImage && <div className="hint">图片已在上方显示</div>}
      </div>
    </div>
  );
}

export function ArtifactsView() {
  const [files, setFiles] = useState([]);
  const [q, setQ] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [showRbac, setShowRbac] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await api.files();
      setFiles(r.files || []);
      setSelected(new Set());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function del(id) {
    if (!confirm("删除这个文件？只做软删除，审计里仍可查。")) return;
    try {
      await api.deleteFile(id);
      load();
    } catch (e) {
      alert(e.message || "删除失败");
    }
  }

  async function batchDelete() {
    const ids = Array.from(selected);
    if (!ids.length) return;
    if (!confirm(`确定彻底删除选中的 ${ids.length} 个产物文件？`)) return;
    setBusyAction("batchDel");
    try {
      const res = await api.batchDeleteFiles(ids);
      setToast(`已批量清理 ${res.deleted || 0} 个产物文件`);
      load();
    } catch (e) {
      setToast(e.message || "批量删除失败");
    } finally {
      setBusyAction("");
      setTimeout(() => setToast(""), 4000);
    }
  }

  async function purgeMissing() {
    setBusyAction("purge");
    try {
      const res = await api.purgeMissingFiles();
      setToast(`已清理 ${res.purged || 0} 个磁盘缺失的无效产物记录`);
      load();
    } catch (e) {
      setToast(e.message || "清理失败");
    } finally {
      setBusyAction("");
      setTimeout(() => setToast(""), 4000);
    }
  }

  function toggleSelect(id) {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  }

  function toggleAll() {
    if (selected.size === shown.length && shown.length > 0) {
      setSelected(new Set());
    } else {
      setSelected(new Set(shown.map((f) => f.id)));
    }
  }

  const shown = files.filter((f) => {
    if (q && !(f.name + f.title).toLowerCase().includes(q.toLowerCase())) return false;
    if (typeFilter === "all") return true;
    const ext = "." + (f.name || "").split(".").pop().toLowerCase();
    if (typeFilter === "code") return [".py", ".js", ".ts", ".sh", ".sql", ".ps1"].includes(ext);
    if (typeFilter === "doc") return [".md", ".txt", ".pdf", ".docx"].includes(ext);
    if (typeFilter === "web") return [".html", ".htm"].includes(ext);
    if (typeFilter === "data") return [".json", ".csv", ".yaml", ".yml"].includes(ext);
    return true;
  });

  return (
    <div className="main">
      <div className="topbar">
        <div className="avatar">文</div>
        <div>
          <div className="who">文件库</div>
          <div className="sub">{files.length} 个产物 · 下载 / 预览 / 批量清理</div>
        </div>
        <div className="spacer" />
        <div className="seg" style={{ margin: 0, width: "auto" }}>
          {[
            ["all", "全部"],
            ["code", "💻 代码"],
            ["doc", "📄 文档"],
            ["web", "🌐 网页"],
            ["data", "📊 数据配置"]
          ].map(([key, label]) => (
            <button key={key} type="button" className={cls("seg-btn", typeFilter === key && "on")}
                    onClick={() => setTypeFilter(key)}>
              {label}
            </button>
          ))}
        </div>
        <input className="search" placeholder="搜索文件名或任务…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className={cls("btn", showRbac && "primary")} onClick={() => setShowRbac(!showRbac)} title="展开/收起 RBAC 演练目录">
          {showRbac ? "隐藏 RBAC 目录" : "RBAC 演练目录"}
        </button>
        <button className="btn ghost" disabled={busyAction === "purge"} onClick={purgeMissing} title="清理数据库中登记但磁盘文件已被删除的孤儿记录">
          {busyAction === "purge" ? "清理中…" : "清理缺失文件"}
        </button>
        <button className="btn ghost" onClick={load}>刷新</button>
      </div>

      {toast && <div className="note-bar" style={{ margin: "10px 24px 0" }}>{toast}</div>}

      <div className="stream">
        <div className="stream-inner">
          {showRbac && (
            <div style={{ marginBottom: 16, padding: 12, background: "var(--panel)", borderRadius: 10, border: "1px solid var(--border)" }}>
              <ResourceSection />
            </div>
          )}

          {/* 批量操作工具条 */}
          {shown.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 12px", background: "var(--panel-2)", borderRadius: 8, marginBottom: 10, fontSize: 13 }}>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={shown.length > 0 && selected.size === shown.length}
                  onChange={toggleAll}
                />
                <span>全选当前 ({selected.size}/{shown.length})</span>
              </label>
              {selected.size > 0 && (
                <>
                  <div className="spacer" />
                  <button className="btn danger" disabled={busyAction === "batchDel"} onClick={batchDelete}>
                    {busyAction === "batchDel" ? "删除中…" : `批量删除选中项 (${selected.size})`}
                  </button>
                  <button className="btn ghost" onClick={() => setSelected(new Set())}>取消选择</button>
                </>
              )}
            </div>
          )}

          {shown.length === 0 && <div className="empty">还没有产物。让某个 Agent 干点活试试。</div>}
          {shown.map((f) => {
            const ext = ("." + f.name.split(".").pop()).toLowerCase();
            const canPreview = PREVIEWABLE.includes(ext);
            return (
              <div className="file-row" key={f.id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <input
                  type="checkbox"
                  checked={selected.has(f.id)}
                  onChange={() => toggleSelect(f.id)}
                  style={{ cursor: "pointer" }}
                />
                <span className="ico">
                  {ext === ".html" ? "🌐" : ext === ".py" ? "🐍" : [".json", ".yaml", ".yml"].includes(ext) ? "⚙" : [".png", ".jpg", ".jpeg", ".svg"].includes(ext) ? "🖼" : "📄"}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <b>{f.name}</b>
                    <span className={cls("tag", !f.size && "bad")}>{f.size ? `${f.size} B` : "文件缺失"}</span>
                  </div>
                  <div className="role">{f.title} · {f.task_id} · {f.created_at}</div>
                </div>
                <div className="spacer" />
                {canPreview && (
                  <button className="btn" onClick={() => setPreview(f)}>预览</button>
                )}
                <a className="btn ghost" href={api.fileUrl(f.id)} target="_blank" rel="noreferrer">下载</a>
                <button className="btn danger" onClick={() => del(f.id)}>删除</button>
              </div>
            );
          })}
        </div>
      </div>
      {preview && <PreviewModal file={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

// Keep FilesTab alias for compatibility
export const FilesTab = ArtifactsView;
export default ArtifactsView;
