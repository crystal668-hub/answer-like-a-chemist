import { realpathSync, statSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";

const EXEC_TOOL_NAMES = new Set(["exec", "execute", "shell", "bash", "command"]);

function isContained(root, candidate) {
  const rel = relative(root, candidate);
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel));
}

export function validateExecWorkdir({ workspaceRoot, workdir }) {
  if (typeof workdir !== "string" || workdir.trim() === "") {
    return { ok: true };
  }

  let resolvedRoot;
  try {
    resolvedRoot = realpathSync(workspaceRoot);
  } catch {
    return { ok: false, reason: "configured benchmark workspace is unavailable" };
  }

  const requested = workdir.trim();
  const candidate = isAbsolute(requested) ? requested : resolve(resolvedRoot, requested);
  let resolvedCandidate;
  try {
    if (!statSync(candidate).isDirectory()) {
      return { ok: false, reason: "exec.workdir is not a directory" };
    }
    resolvedCandidate = realpathSync(candidate);
  } catch {
    return { ok: false, reason: "exec.workdir does not exist" };
  }

  if (!isContained(resolvedRoot, resolvedCandidate)) {
    return { ok: false, reason: "exec.workdir is outside the current benchmark workspace" };
  }
  return { ok: true };
}

export default {
  id: "benchmark-workdir-guard",
  name: "Benchmark Workdir Guard",
  description: "Blocks invalid explicit exec workdirs before benchmark commands run.",
  register(api) {
    const configured = api.pluginConfig?.agentWorkspaces;
    const agentWorkspaces = configured && typeof configured === "object" ? configured : {};
    api.on(
      "before_tool_call",
      (event, context) => {
        if (!EXEC_TOOL_NAMES.has(String(event.toolName || "").toLowerCase())) {
          return;
        }
        const agentId = String(context.agentId || "");
        const workspaceRoot = agentWorkspaces[agentId];
        if (typeof workspaceRoot !== "string" || workspaceRoot.trim() === "") {
          return;
        }
        const validation = validateExecWorkdir({
          workspaceRoot,
          workdir: event.params?.workdir,
        });
        if (validation.ok) {
          return;
        }
        return {
          block: true,
          blockReason: [
            `benchmark_workdir_invalid: ${validation.reason}.`,
            "The command was not executed.",
            'Omit exec.workdir and begin the command with cd "$BENCHMARK_SKILL_SCRATCH_DIR" &&.',
          ].join(" "),
        };
      },
      { priority: 100 },
    );
  },
};
