import { useEffect, useState } from "react";
import { Download, HardDrive, LoaderCircle, Trash2, X } from "lucide-react";
import { api } from "../api";
import type { StorageSummary } from "../types";

export function formatBytes(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

export default function StorageDialog({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<StorageSummary>();
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setData(await api.storage());
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法读取存储信息");
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const cleanup = async () => {
    if (!window.confirm("只清理临时文件并压缩数据库，不会删除任务。继续？")) return;
    setBusy(true);
    setError("");
    setNote("");
    try {
      const result = await api.cleanup();
      setData(result);
      if (result.warning) setNote(result.warning);
      else setNote(`已释放 ${formatBytes(result.freed_bytes ?? 0)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "清理失败");
    } finally {
      setBusy(false);
    }
  };

  const total = data
    ? data.outputs_bytes + data.checkpoint_bytes + data.work_bytes + data.logs_bytes
    : 0;
  const tiles = data
    ? [
        ["任务结果与证据", formatBytes(data.outputs_bytes)],
        ["任务检查点", formatBytes(data.checkpoint_bytes)],
        ["运行缓存", formatBytes(data.work_bytes)],
        ["日志", formatBytes(data.logs_bytes)],
      ]
    : [];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <section
        className="modal storage-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="storage-title"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          className="icon-button storage-close"
          aria-label="关闭数据与存储"
          onClick={onClose}
          autoFocus
        >
          <X size={18} />
        </button>
        <header className="storage-header">
          <span className="modal-icon">
            <HardDrive size={22} />
          </span>
          <div>
            <h2 id="storage-title">数据与存储</h2>
            <p>本地数据备份与空间清理</p>
          </div>
        </header>
        {!data && !error && (
          <p className="storage-loading" role="status">
            <LoaderCircle size={16} className="spin" /> 正在统计…
          </p>
        )}
        {error && <p className="warning-text">{error}</p>}
        {data && (
          <>
            <div className="storage-total">
              <span>共占用</span>
              <strong>{formatBytes(total)}</strong>
              <em>{data.tasks} 个任务</em>
            </div>
            <div className="storage-tiles">
              {tiles.map(([label, value]) => (
                <div className="storage-tile" key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
          </>
        )}
        {note && <p className="storage-note">{note}</p>}
        <div className="storage-actions">
          <a className="primary" href="/backup" download>
            <Download size={16} />
            下载完整备份
          </a>
          <button className="secondary" disabled={busy} onClick={() => void cleanup()}>
            {busy ? <LoaderCircle size={16} className="spin" /> : <Trash2 size={16} />}
            清理临时文件并压缩
          </button>
        </div>
        <p className="storage-hint">
          备份包含任务结果、来源、记忆库和检查点快照；不包含密钥、evidence、日志和运行缓存。清理不会删除任务。
        </p>
        <details className="storage-restore">
          <summary>如何恢复备份</summary>
          <p>先关闭工作台，再运行：</p>
          <code>.\.venv\Scripts\python.exe scripts\restore_backup.py "备份文件.zip" --yes</code>
          <p>恢复前会自动备份当前数据，失败会回滚。</p>
        </details>
      </section>
    </div>
  );
}
