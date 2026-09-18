import { bytes, number } from "./format.js";

export function drawResourceChart(canvas, points = []) {
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(320, canvas.clientWidth);
  const height = Math.max(180, canvas.clientHeight);
  canvas.width = width * ratio; canvas.height = height * ratio;
  const context = canvas.getContext("2d"); context.scale(ratio, ratio); context.clearRect(0, 0, width, height); context.font = "11px IBM Plex Mono, monospace";
  if (!points.length) { context.fillStyle = "#7b8792"; context.fillText("No resource samples", 16, 28); return; }
  const margin = { top: 18, right: 18, bottom: 28, left: 52 }; const innerWidth = width - margin.left - margin.right; const innerHeight = height - margin.top - margin.bottom;
  const memoryPeak = Math.max(1, ...points.map((item) => Number(item.memory_peak_bytes || 0))); const cpuPeak = Math.max(100, ...points.map((item) => Number(item.cpu_peak_percent || 0)));
  context.strokeStyle = "#d5dce1"; context.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) { const y = margin.top + (innerHeight * i) / 4; context.beginPath(); context.moveTo(margin.left, y); context.lineTo(width - margin.right, y); context.stroke(); }
  const x = (index) => margin.left + (innerWidth * index) / Math.max(1, points.length - 1);
  const line = (field, peak, color) => { context.beginPath(); points.forEach((point, index) => { const y = margin.top + innerHeight - (Number(point[field] || 0) / peak) * innerHeight; if (index === 0) context.moveTo(x(index), y); else context.lineTo(x(index), y); }); context.strokeStyle = color; context.lineWidth = 2; context.stroke(); };
  line("cpu_peak_percent", cpuPeak, "#d34836"); line("memory_peak_bytes", memoryPeak, "#137d66"); context.fillStyle = "#53606a"; context.fillText(`${number(cpuPeak, 0)}% CPU`, 4, margin.top + 4); context.fillText(bytes(memoryPeak), 4, margin.top + 18); context.fillText("CPU", margin.left, height - 7); context.fillStyle = "#137d66"; context.fillText("Memory", margin.left + 34, height - 7);
}
