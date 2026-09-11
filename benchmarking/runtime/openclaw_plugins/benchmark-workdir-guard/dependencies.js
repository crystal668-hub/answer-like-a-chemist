import { basename } from "node:path";

// Parse only literal shell words. Dynamic dependency arguments fail closed.
export function shellCommands(command) {
  const commands = []; let words = [], word = "", quote = "", escaped = false;
  const flush = () => { if (word) { words.push(word); word = ""; } };
  for (const char of command) {
    if (escaped) { word += char; escaped = false; continue; }
    if (char === "\\" && quote !== "'") { escaped = true; continue; }
    if (quote) { if (char === quote) quote = ""; else word += char; continue; }
    if (char === "'" || char === '"') { quote = char; continue; }
    if (";&|\n".includes(char)) { flush(); if (words.length) commands.push(words); words = []; }
    else if (/\s/.test(char)) flush(); else word += char;
  }
  flush(); if (words.length) commands.push(words);
  return { commands, complete: !quote && !escaped };
}

const protectedVariable = /^(?:VIRTUAL_ENV|UV_[A-Z_]+|PIP_[A-Z_]+|PYTHONPATH|PYTHONHOME|BENCHMARK_ATTEMPT_[A-Z_]+|BENCHMARK_PYPI_CUTOFF)=/;
const denied = reason => ({ ok: false, access: "dependency", reason });

export function validateDependencyCommand(command) {
  if (!process.env.BENCHMARK_ATTEMPT_PYTHON || typeof command !== "string") return { ok: true };
  const parsed = shellCommands(command);
  for (const original of parsed.commands) {
    const words = [...original]; let override = false;
    if (words[0] === "command") words.shift();
    if (basename(words[0] || "") === "env") words.shift();
    if (words[0]?.startsWith("-") && original.some(w => /^(?:uv|pip\d*|python\d*)$/.test(basename(w)))) return denied("environment options for dependency commands are unsupported");
    while (words.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[0])) {
      const assignment = words.shift();
      override = protectedVariable.test(assignment) || override;
    }
    const executable = basename(words.shift() || "");
    if (override) return denied("attempt dependency policy variables cannot be overridden");
    if (["export", "unset"].includes(executable) && words.some(w => protectedVariable.test(w.includes("=") ? w : w + "="))) return denied("attempt dependency policy variables cannot be overridden");
    if (["sh", "bash", "zsh"].includes(executable) && words[0] === "-c") {
      const nested = validateDependencyCommand(words[1]);
      if (!nested.ok) return nested;
    }
    const isPython = /^python(?:\d+(?:\.\d+)*)?$/.test(executable) || executable === "$BENCHMARK_ATTEMPT_PYTHON" || executable === "${BENCHMARK_ATTEMPT_PYTHON}";
    const moduleIndex = words.indexOf("-m");
    const isPip = /^pip(?:\d+(?:\.\d+)*)?$/.test(executable) || (isPython && moduleIndex >= 0 && words[moduleIndex + 1] === "pip");
    if (isPip && /\b(?:install|uninstall|download|wheel)\b/.test(words.join(" "))) {
      return denied("pip mutations are disabled; use uv pip with the attempt environment");
    }
    if (executable !== "uv") continue;
    if (!parsed.complete || override || /[$`]/.test(original.join(" "))) return denied("attempt dependency policy or target cannot be overridden or dynamically expanded");
    if (words[0] !== "pip") {
      if (words[0]?.startsWith("-")) return denied("uv global dependency overrides are unsupported");
      if (["sync", "add", "remove", "tool", "venv"].includes(words[0]) || words.some(w => w.startsWith("--with"))) {
        return denied("dependency changes must use uv pip with the attempt environment");
      }
      continue;
    }
    const operation = words[1];
    if (["list", "freeze", "show", "check", "tree"].includes(operation)) continue;
    if (!["install", "uninstall"].includes(operation)) return denied("unsupported dependency operation");
    for (const argument of words.slice(2)) {
      if (/^(?:-r|-c|--requirement|--constraint|--build-constraint)/.test(argument)) return denied("requirements and constraints inputs are not supported");
      if (/^(?:--python|--system|--target|--prefix|--project)/.test(argument)) return denied("the attempt dependency target cannot be overridden");
      if (argument.startsWith("-") || /(?:\/|\\|@|:)/.test(argument) || argument === "." || argument === "..") return denied("only packages from the configured PyPI registry are allowed");
      if (/\.(?:whl|zip|gz)$/i.test(argument) || !/^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?(?:[<>=!~]{1,3}[A-Za-z0-9.*+_-]+(?:,[<>=!~]{1,3}[A-Za-z0-9.*+_-]+)*)?$/.test(argument)) return denied("unsupported registry requirement");
      const name = argument.split(/[<>=!~\[]/, 1)[0].toLowerCase().replace(/[-_.]+/g, "-");
      if (name === "verifier-grounded-benchmark") return denied("the requested distribution is forbidden in benchmark attempts");
    }
  }
  return { ok: true };
}
