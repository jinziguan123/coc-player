/** 模型下载 / 索引重建的进度条（检索模型与精排模型设置卡共用）。 */
export function ModelDownloadProgress({ done, total, label }: { done: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  return (
    <div style={{ marginTop: '0.4rem' }}>
      <div
        className="upload-progress-track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className="upload-progress-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
