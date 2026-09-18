export function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;"); }
export function escapeAttribute(value) { return escapeHtml(value); }
function missing(value) { return value === null || value === undefined || value === ""; }
export function number(value, digits = 1) { if (missing(value)) return "—"; const parsed = Number(value); return Number.isFinite(parsed) ? parsed.toLocaleString(undefined, { maximumFractionDigits: digits }) : "—"; }
export function duration(value) { if (missing(value)) return "—"; const seconds = Number(value); if (!Number.isFinite(seconds)) return "—"; if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`; return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`; }
export function bytes(value) { if (missing(value)) return "—"; let amount = Number(value); if (!Number.isFinite(amount)) return "—"; const units = ["B", "KiB", "MiB", "GiB", "TiB"]; let index = 0; while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; } return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`; }
export function percent(value) { if (missing(value)) return "—"; const parsed = Number(value); return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(1)}%` : "—"; }
export function compact(value) { if (missing(value)) return "—"; const parsed = Number(value); if (!Number.isFinite(parsed)) return "—"; if (Math.abs(parsed) >= 1_000_000) return `${(parsed / 1_000_000).toFixed(1)}M`; if (Math.abs(parsed) >= 1_000) return `${(parsed / 1_000).toFixed(1)}K`; return number(parsed, 0); }
export function coverageBadge(value) { const status = String(value || "unavailable"); return `<span class="coverage ${escapeAttribute(status)}">${escapeHtml(status)}</span>`; }
export function statusBadge(value) { const status = String(value || "unknown"); return `<span class="status-badge ${escapeAttribute(status)}"><i></i>${escapeHtml(status)}</span>`; }
export function code(value) { return `<code>${escapeHtml(value || "")}</code>`; }
