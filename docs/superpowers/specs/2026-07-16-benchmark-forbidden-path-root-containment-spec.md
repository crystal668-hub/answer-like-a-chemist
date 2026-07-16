# Benchmark Forbidden Path Root Containment Specification

状态：`DONE`

日期：2026-07-16

适用项目：OpenClaw benchmark attempt workspace contamination audit

## 1. 摘要

当前 benchmark transcript audit 同时使用路径 containment 和敏感名称匹配判定污染。
`formal-benchmarks`、`verifier-grounded`、`task.yaml`、`gold`、
`sample_answers.jsonl` 等字符串只要出现在完整 `exec` 参数中，就可能在没有访问任何受保护资源
时触发污染。最新 VGB RDKit run 的合法 scratch 路径包含
`verifier-grounded-rdkit-...`，因此被名称规则误判，attempt 随后 fail closed，最终造成 11 题
全部无答案。

本规格用“解析后的真实路径是否位于显式受保护 root”替换全部名称型判断。目录名和文件名不再
携带安全语义；`formal-benchmarks` 只有在它是当前实际配置的 benchmark dataset root 时才受
保护。

核心判定顺序固定为：

```text
提取确定的路径表达式
-> 精确展开环境变量和 ~
-> 按实际执行上下文 resolve
-> 若位于 allowed scope，允许
-> 否则若位于显式 protected root，报告 forbidden_path
-> 否则不产生路径污染 finding
```

本规格只重构 transcript audit 的路径分类，不改变 benchmark 数据内容、评分接口、workspace
生命周期或 fail-closed 总体策略。

## 2. 背景与根因

`benchmarking/runtime/agent_workspace.py` 当前存在两套并行规则：

1. `_SENSITIVE_RESOURCE_PATTERNS` 扫描序列化后的工具参数；
2. `_candidate_paths()` 解析路径，再与 `forbidden_roots` 做 containment。

名称扫描先于 allowed-root 判断，因此即使路径位于当前 active workspace、scratch 或公开 input
bundle，只要其中包含敏感词也会被拒绝。该实现还有以下结构性问题：

- `formal-benchmarks` 按名字保护，无法区分真实 dataset root 与任意同名 scratch 目录；
- 自定义 `OPENCLAW_BENCHMARKS_ROOT` 若不含敏感词，可能漏检；
- `output_root.parent` 把当前输出目录的所有兄弟目录一并纳入，使用
  `--exact-output-dir` 时保护范围过宽；
- `runtime_root.parents[2] / "agents"` 依赖固定目录深度，不代表真实 `agents_root`；
- `task.yaml`、`gold/`、`sample_answers.jsonl` 是普通名称，在合法代码、fixture 和 scratch 中均
  可能出现；
- `_expand_environment()` 使用字符串替换，变量名前缀可能互相影响；
- `_candidate_paths()` 丢弃以 `-` 开头的 token，因而漏掉 `--input=/absolute/path`。

边界感知正则只能缩小误判面，不能解决“名称不是资源身份”这一根因。提交 `b36cdfe` 是针对首轮
误判的过渡修复，不是本规格的最终策略。

## 3. 目标

### 3.1 功能目标

- 只有解析后的 candidate path 落入显式 protected root 时，才报告 forbidden path；
- 当前 active workspace、scratch、可信 skill scope 和当前 input bundle 始终优先允许；
- 默认和自定义 benchmark dataset root 使用同一策略；
- 当前 run output、历史 run output、其他 attempt workspace、agent state 和 verifier 私有资源继续
  受到保护；
- absolute path、relative traversal、环境变量、`~`、symlink 和 `--flag=/path` 使用统一解析
  契约；
- finding 能证明“哪个 resolved path 命中了哪个配置 root”，便于复核和回归。

### 3.2 质量目标

- 策略来源可枚举、可序列化、可测试，不从路径名称猜测；
- root containment 的结果不依赖 benchmark run id、track 名、题目文件名或模型输出文字；
- production manager 不依赖固定 parent 层数推导外部目录；
- 测试 manager 可显式注入临时 roots，不隐式读取本机 production state；
- 对同一个 candidate 和同一组 policy，判定结果稳定且与列表顺序无关。

## 4. 非目标与威胁模型

### 4.1 非目标

- 不实现完整 POSIX shell parser；
- 不执行命令、command substitution、glob 或 shell parameter expansion 来猜测动态路径；
- 不用 transcript audit 证明某个文件最终发生了 syscall 级读取；
- 不通过本次修改提供 OS filesystem sandbox、容器或独立用户隔离；
- 不按内容扫描 agent 创建的文件，也不禁止普通文本提及 benchmark 或 verifier 名称；
- 不自动搜索本机所有名为 `formal-benchmarks`、`gold` 或
  `verifier-grounded-benchmark` 的目录；
- 不扩大为“active workspace 之外全部禁止”的通用文件访问策略。

### 4.2 威胁模型

本规格继续面向 benchmark 污染审计：检测 transcript 中对已知 dataset、verifier、run output、
agent state 和历史 workspace 的路径引用。agent 可能使用绝对路径、`..`、环境变量或已有 symlink
尝试访问这些 roots。

Transcript audit 是静态、保守的意图审计，不是系统调用审计。它能证明 finding 中的路径属于
策略声明的禁止树，但不能证明命令确实成功读取该路径；同样，动态构造且 transcript 中没有确定
路径的访问可能漏检。需要强对抗边界时，必须另行引入 OS sandbox。

## 5. 核心不变量

### BFRC-01：禁止路径必须有 root 证据

对每个 `forbidden_path` finding，必须存在 candidate `p` 和配置项 `r`，满足：

```text
p == finding.resolved_path
r.path == finding.matched_root
p is within r.path
```

`r` 必须来自 manager 构造时注入的 `protected_roots`。不得由 transcript 中的名称、正则命中或
candidate 的 basename 临时生成。

### BFRC-02：Allowed 优先

若 resolved candidate 位于任一 allowed scope，必须直接允许，不再检查 protected roots。该规则
用于允许嵌套在 current output root 下的公开 input bundle，以及嵌套在 benchmark runtime root 下
的当前 active workspace。

### BFRC-03：名称无策略语义

以下文本单独出现时不得产生 `forbidden_path`：

- `formal-benchmarks`；
- `verifier-grounded` 或 `verifier_grounded`；
- `task.yaml` / `task.yml`；
- `gold`；
- `sample_answers`；
- `verifier_specs`。

它们只有作为 candidate path 的组成部分，且 resolved path 命中 protected root 时才可能被拒绝。

### BFRC-04：Root 来源显式

Production protected roots 必须直接来自 `runtime_paths`、当前 manager 参数或显式列出的 legacy
路径。不得使用 `parents[n]`、basename 搜索、glob 或当前目录形状推导策略 root。

### BFRC-05：真实路径比较

Candidate、allowed scope 和 protected root 在比较前都必须 `expanduser` 后
`resolve(strict=False)`。已有 symlink 前缀必须解析到真实目标；containment 必须使用路径组件
比较，禁止使用字符串 `startswith` 或 substring。

### BFRC-06：确定性匹配

一个 candidate 命中多个 protected roots 时，选择组件层级最深的 root。深度相同则按
`policy_id` 字典序选择。finding 不得因调用方提供列表的顺序改变。

### BFRC-07：无证据不定罪

无法提取为确定 candidate path 的普通文本不产生 path finding。解析器异常或 transcript 损坏按
现有 `audit unavailable` 契约 fail closed，但不得伪造 `forbidden_path`。

## 6. 路径策略数据模型

### 6.1 ProtectedRoot

在 `benchmarking/runtime/agent_workspace.py` 增加不可变策略对象：

```python
@dataclass(frozen=True)
class ProtectedRoot:
    policy_id: str
    path: Path
    source: str
```

字段约束：

- `policy_id` 是稳定机器标识，写入 finding 和 runtime manifest；
- `path` 表示一棵受保护目录树，构造时转换为 absolute resolved path；
- `source` 记录策略来源，例如 `runtime_paths.benchmarks_root`，仅用于审计；
- 空路径、filesystem root、非目录的既有路径和空 `policy_id` 必须在 benchmark preflight 被拒绝；
- 不存在但由 canonical 配置声明的 root 可以保留，用于保护后续创建的同一保留路径；
- 重复 `(policy_id, resolved path)` 去重；同一 `policy_id` 指向不同路径是合法的，仅用于显式
  legacy 或多 source roots；
- 同一路径对应多个 policy 时允许存在，按 BFRC-06 选择稳定结果。

`AttemptWorkspaceManager.__init__()` 增加必需参数 `protected_roots`。manager 只消费已解析的策略，
不在内部 import `runtime_paths`，保证测试和 production 依赖清晰。

### 6.2 Production protected roots

正式 benchmark CLI 必须构造并注入下列 roots：

| `policy_id` | 真实路径来源 | 保护原因 |
| --- | --- | --- |
| `benchmark_dataset_root` | `runtime_paths.benchmarks_root` | 完整 benchmark records 和非当前 record 数据 |
| `temp_benchmark_dataset_root` | `runtime_paths.temp_benchmarks_root` | 临时 benchmark records 和历史测试数据 |
| `verifier_release_root` | `runtime_paths.data_root / "verifier-grounded-releases"` | pinned wheel、release manifest 和 verifier package |
| `verifier_runtime_root` | `runtime_paths.project_state_root / "verifier-grounded-runtimes"` | scorer venv、runtime manifest 和 verifier 安装 |
| `verifier_resource_root` | `runtime_paths.project_root / "benchmarking/resources/verifier_grounded"` | tracked dataset snapshots 和 release inventory |
| `benchmark_runtime_root` | manager 的 `runtime_root` | 其他 run、invocation、group 和 agent workspace；当前 active workspace由 allowed scope 豁免 |
| `benchmark_results_root` | `runtime_paths.project_state_root / "benchmark-runs"` | canonical 历史和当前 benchmark results |
| `current_output_root` | manager 的 `output_root` | 当前 run 的结果、progress、archive 和 quarantine；公开 bundle由 allowed scope 豁免 |
| `agents_root` | `runtime_paths.agents_root` | agent sessions、transcripts 和历史 state |
| `legacy_benchmark_workspace` | 6.3 的每个显式路径 | 旧固定 workspace 的历史产物 |

如果存在额外 verifier source checkout，只能通过显式配置追加
`verifier_source_checkout` root；不得通过目录名搜索自动发现。

### 6.3 Legacy roots

Legacy roots 直接由 `runtime_paths.benchmark_runtime_root` 拼接固定相对路径：

```text
benchmark-single-skills-on
benchmark-single-skills-off
benchmark-judge
custom-single-agent
```

禁止继续用 `manager.runtime_root.parent` 或 `parents[n]` 还原这些目录。即使 production runtime
layout 以后变化，legacy policy 仍由 canonical runtime path 明确表达。

### 6.4 为什么保护 formal-benchmarks

真实 `runtime_paths.benchmarks_root` 包含完整数据集，而 agent 的输入契约只公开当前 record 及其
当前 input bundle。直接读取 dataset root 可能暴露其他 records、grading config 或未来新增的非公开
字段，因此该真实目录必须保护。

被保护的是这个 configured root，不是字符串 `formal-benchmarks`：

- 默认 root `/.../data/formal-benchmarks/...`：命中时禁止；
- 自定义 root `/mnt/vgb-datasets/...`：即使不含敏感词也禁止；
- scratch 中的 `formal-benchmarks-copy/`：只要不落入 protected root 就不禁止；
- `/tmp/formal-benchmarks/...`：若没有被显式配置为 root，就不因名称禁止。

### 6.5 Allowed scopes

每次 audit 的 allowed scopes 至少包括：

- 当前 `lease.active_workspace`，包含当前 scratch；
- 当前 record 的公开 input bundle；
- runner 显式公开的 skill root；
- skills-on 调用所需的 canonical `scripts/run_skill.py` exact path。

Allowed scope 必须由可信 runner 代码注入，不能来自 transcript。不得为了减少 finding 而允许整个
`project_root`、`data_root`、`state` 或 `output_root`。

Allowed scope 可以位于 protected root 内。此重叠是预期设计，不是配置错误；BFRC-02 保证只豁免
精确公开的子树，不豁免其父目录或兄弟目录。

## 7. Candidate 路径契约

### 7.1 数据结构

`_candidate_paths()` 改为返回带来源信息的记录，而不是裸 `Path`：

```python
@dataclass(frozen=True)
class PathCandidate:
    raw_token: str
    expanded_token: str
    resolved_path: Path
    source: str
```

`source` 至少区分 `exec.command`、`exec.workdir` 和结构化工具 argument key。原始或展开 token 只在
进程内用于判断；持久化前必须沿用现有 secret redaction。

### 7.2 工具参数范围

- 对 `exec` / `execute` / `shell` / `bash` / `command`，解析 `command` 文本和显式
  `workdir` / `cwd`；
- 对 `read`、`write`、`edit` 等结构化文件工具，只解析 path-bearing argument，例如 `path`、
  `file_path`、`directory`、`workdir`、`cwd`、`source` 和 `target`；
- `content`、`body`、`text`、`data`、`old_text`、`new_text`、`replacement` 等 payload 字段不作为
  path source；
- 未识别工具只解析明确 path-bearing key，不把整个 JSON 序列化后扫描；
- web search query、prompt 文本和 tool result 文本不做 forbidden path 扫描；workdir fallback
  finding 继续由独立规则处理。

### 7.3 Exec token 提取

审计器使用 heredoc-aware projection 加 `shlex` 做 lexical tokenization，不构建完整 shell AST。以下
token 形式必须支持：

- `/absolute/path`；
- `./relative/path`、`../relative/path` 和包含 `..` path component 的相对路径；
- `~/path`；
- `$VAR/path` 和 `${VAR}/path`；
- `--flag=/absolute/path`、`NAME=/absolute/path` 中 `=` 右侧的 path expression；
- shell 标点相邻的 quoted/unquoted path token。

对 `<<` / `<<-` heredoc，projection 必须识别 quoted/unquoted delimiter 和同一命令声明的多个
delimiter。Heredoc body 不是 shell 语法，不能直接交给 `shlex`；审计器将其中的引号和嵌入语言
控制符转换为空白，同时保留可确定的 path 字符，使 Python 三引号、普通单双引号或类似 shell 的
文本不会造成 `No closing quotation`，但 body 中明确写出的 protected absolute/env/relative path
仍以 `candidate_source=exec.heredoc` 进入 containment 判定。`<<<` here-string 不得当作 heredoc。
声明后缺失终止 delimiter 仍属于 parser failure，audit unavailable。

HTTP(S) URL、普通英文、run id 和不含路径语法的文件名不作为 candidate。`shlex` 无法处理的
输入不得退回“扫描完整字符串中的敏感词”；应只使用可确定提取的 token，解析器本身异常则令
audit unavailable。

### 7.4 环境变量与 home 展开

环境变量展开必须按 shell identifier 边界处理：

```text
$VAR
${VAR}
```

- 变量名匹配 `[A-Za-z_][A-Za-z0-9_]*`；
- `$HOME2` 不得被 `$HOME` 的值部分替换；
- 值只来自本 attempt 传给 OpenClaw subprocess 的 `environment` mapping；
- `~` 和 `~/...` 只使用同一 mapping 中的 `HOME`；
- 不调用当前审计进程的隐式 `Path.home()` 作为 fallback；
- 不支持 `~otheruser`、`${VAR:-default}`、`$()` 或反引号执行；
- 一个 token 在所需变量未知或使用不支持的动态表达式时，不生成确定 candidate，也不得按名称
  判污染。

### 7.5 Resolve 上下文

- absolute candidate 直接 `resolve(strict=False)`；
- `exec.workdir` / `cwd` 先相对当前 active workspace resolve；
- command 中的 relative candidate 默认相对 active workspace resolve；在简单线性
  `cd <determinate-path> && ...` 或 `cd <determinate-path>; ...` 链中，后续 relative candidate
  相对该有效目录 resolve，连续 `cd` 依次更新；
- 已有 symlink 前缀必须解析，确保 workspace 外的 symlink alias 不能绕过 containment；
- `.`、`..` 必须按 path component 归一化；
- 不通过字符串替换规范化斜杠或 containment。

动态 `cd`、`cd -`、条件分支、pipeline 或 subshell 不能确定唯一 cwd 时，审计器把 relative base
视为未知而不伪造 candidate；absolute candidate、`cd` 本身可确定的目标 path 和 OpenClaw
`workdir` 仍会被提取。更强的命令执行语义属于 OS sandbox 或后续 shell AST 工作。

## 8. 判定算法

对每个 tool call：

```python
candidates = extract_path_candidates(tool_name, arguments, environment, workspace)
allowed = normalize_allowed_scopes(lease.active_workspace, allowed_scopes)
protected = normalize_protected_roots(manager.protected_roots)

for candidate in candidates:
    if any(is_within(candidate.resolved_path, scope.path) for scope in allowed):
        continue

    matches = [
        root for root in protected
        if is_within(candidate.resolved_path, root.path)
    ]
    if matches:
        matched = most_specific_then_policy_id(matches)
        emit_forbidden_path(candidate, matched)
```

约束：

- `is_within()` 使用 `Path.relative_to()` 等价的组件 containment；
- protected root 比较前不得检查 candidate 名称；
- 同一 tool call 对同一 `(resolved_path, policy_id)` 去重；
- 不同 tool calls 的 finding 保留，以便 trajectory 定位；
- finding 顺序按 transcript 顺序稳定输出。

## 9. Finding schema

路径污染 finding 统一为：

```json
{
  "rule_id": "forbidden_path",
  "policy_id": "benchmark_dataset_root",
  "tool_name": "exec",
  "candidate_source": "exec.command",
  "resolved_path": "/Users/example/.openclaw/data/formal-benchmarks/track/data/tasks.jsonl",
  "matched_root": "/Users/example/.openclaw/data/formal-benchmarks",
  "command_excerpt": "cat $OPENCLAW_BENCHMARKS_ROOT/track/data/tasks.jsonl"
}
```

要求：

- 删除名称型 `verifier_or_gold_path` finding；
- `policy_id`、`resolved_path`、`matched_root` 对 `forbidden_path` 为必需字段；
- `matched_root` 必须与 manager manifest 中某个 protected root 完全相等；
- `resolved_path` 必须位于 `matched_root`，且不位于任何 allowed scope；
- `command_excerpt` 保留现有长度限制和 secret redaction；
- finding 不保存完整 environment 或未脱敏工具参数；
- `workdir_fallback`、`transcript_unavailable` 和 `transcript_audit_failed` 保持独立 `rule_id`，不伪装
  成 path policy。
- `transcript_audit_failed` 在可定位到 tool call 时额外保存 `transcript_line`、`tool_name`、脱敏后的
  `command_excerpt`、`exception_type` 和脱敏后的 `exception_message`，方便区分 transcript 损坏、
  heredoc/parser 缺陷和其他内部异常；这些诊断不改变 unavailable 的 fail-closed 语义。

Runtime manifest 的 `workspace_isolation` 增加：

```json
{
  "forbidden_path_policy": {
    "schema_version": 1,
    "protected_roots": [
      {
        "policy_id": "benchmark_dataset_root",
        "path": "/resolved/path",
        "source": "runtime_paths.benchmarks_root"
      }
    ]
  }
}
```

该清单用于事后证明 run 当时采用的实际策略，包括自定义环境路径。

## 10. Runner 接入

### 10.1 Production CLI

`benchmarking/workflow/cli.py` 在确定 `output_root`、`runtime_paths` 和 manager `runtime_root` 后，构造
完整 `protected_roots` 并一次性注入 `AttemptWorkspaceManager`。配置预览、兼容 runner 和测试 helper
不得隐式拼出 production paths；它们必须显式提供适合自身 scope 的 roots。

CLI runtime manifest 使用 manager 已规范化的 policy 清单，避免展示值与实际判定值不一致。

### 10.2 SingleLLMRunner

保持当前每-attempt audit 时机。动态 allowed scopes 包含：

- lease active workspace；
- 当前 input bundle；
- 当前 group 的 `allowed_workspace_roots`。

Skills-on/off 使用相同 forbidden policy。run id 或 scratch path 含 `verifier-grounded` 不得改变结果。

### 10.3 ChemQARunner

coordinator、proposer、reviewer 的每个 lease 使用同一个 manager policy。每个角色只能豁免自己的
active workspace；当前 runtime bundle 和显式 skill roots 按现有 runner contract 加入 allowed scopes。
一个角色的 active workspace 不得成为另一个角色的 allowed scope。

### 10.4 Judge

Judge 使用同一 protected policy，只允许自己的 active workspace 和 verdict 所需的显式公开输入。
不得允许 current output root 以便“方便读取结果”；judge 输入必须继续通过 prompt/input contract
传入。

### 10.5 Web-search preflight

Web-search preflight 使用同一个 manager 和同一 protected policy。因为其任务不需要本地 benchmark
数据，除自身 active workspace 外不新增 data/output allowed scope。preflight transcript audit 仍按
现有 clean/contaminated/unavailable 语义处理。

## 11. 失败语义

- 找到一个或多个 `forbidden_path`：audit 为 `contaminated`；attempt 不评分；
- transcript 缺失、损坏或 candidate parser 内部异常：audit 为 `unavailable`；attempt fail closed；
- 未识别的动态 shell 表达式：不构造虚假 candidate；记录 debug counter 即可，不单独把正常
  attempt 判为 contaminated；
- protected policy 构造无效：benchmark preflight 失败，不启动任何 agent；
- allowed/protected 重叠：不是失败，按 allowed-first 处理；
- finding schema 无法满足 BFRC-01：视为 auditor implementation error，audit unavailable；
- contamination、unavailable、archive failure 对 `evaluable=false`、`scored=false` 的现有契约不变。

## 12. 测试规格

### 12.1 单元测试矩阵

| 场景 | 预期 |
| --- | --- |
| 当前 scratch 路径包含 `verifier-grounded` | clean |
| run id 为 `verifier-grounded-rdkit-...` | clean |
| scratch 内创建 `task.yaml`、`gold/`、`sample_answers.jsonl` | clean |
| scratch 内目录名为 `formal-benchmarks-copy` | clean |
| 未配置的 `/tmp/formal-benchmarks/...` | 不因名称产生 finding |
| 默认 `runtime_paths.benchmarks_root/...` | contaminated，`policy_id=benchmark_dataset_root` |
| 自定义 `/tmp/custom-vgb-data/...` | contaminated，即使路径无敏感词 |
| `runtime_paths.temp_benchmarks_root/...` | contaminated |
| verifier release/runtime/resource root 下路径 | 分别命中对应 policy |
| 当前 active workspace | clean，即使嵌套在 benchmark runtime root |
| 其他 invocation/agent workspace | contaminated，命中 `benchmark_runtime_root` |
| 当前 input bundle 嵌套在 current output root | clean |
| current output 的非公开 per-record/result/archive | contaminated |
| canonical benchmark results 中其他 run | contaminated |
| `--exact-output-dir=/tmp/runs/run-a` 时访问 `/tmp/runs/unrelated` | clean，证明不再保护 `output_root.parent` |
| `runtime_paths.agents_root/.../sessions.json` | contaminated |
| 四个显式 legacy roots | contaminated |
| `cat /protected/root/file` | contaminated |
| `cat ../../path/to/protected` | resolve 后按 containment 判定 |
| `cat $VAR/file` 与 `cat ${VAR}/file` | 精确展开后按 containment 判定 |
| 同时存在 `HOME`、`HOME2` | 不发生变量前缀替换 |
| `cat ~/path` | 使用 attempt environment 的 `HOME` |
| `--input=/protected/root/file` | contaminated |
| Python heredoc body 含三引号/单双引号且路径均在 current scratch | clean |
| heredoc body 含明确 protected absolute path | contaminated，`candidate_source=exec.heredoc` |
| `<<<` here-string | 不作为 heredoc，不产生 parser failure |
| `cd $SCRATCH/outputs/cand && cat ../../requests/file` | 按 effective cwd resolve 到 current scratch，clean |
| `cd $SCRATCH/outputs/cand && cat <relative protected path>` | 按 effective cwd resolve 后 contaminated |
| existing symlink alias 指向 protected root | resolve 后 contaminated |
| 路径仅出现在 `write.content` 或 `edit.new_text` | 不作为 candidate |
| `echo verifier-grounded` 或问题文本提及 `gold` | clean |
| allowed child 嵌套在 protected parent | clean |
| 同时命中 current output 和 benchmark results | 选择最深 root，结果稳定 |

现有 `test_forbidden_path_audit_still_detects_verifier_resource_paths` 必须拆分并反转其中按名称成立的
断言：`/tmp/formal-benchmarks`、`/tmp/verifier-grounded-benchmark/tasks/task.yaml` 和
`/tmp/sample_answers.jsonl` 在未配置为 protected root 时不得仅凭名称失败。真实 root fixture 继续
断言 contaminated。

### 12.2 集成测试

- Production policy builder 在默认 runtime layout 下生成 9 类 roots 和 4 个 legacy entries；
- 设置 `OPENCLAW_BENCHMARKS_ROOT` 后，manifest 和实际 finding 使用自定义 resolved path；
- SingleLLM skills-on/off、ChemQA role、judge 和 web-search preflight 都消费同一个 manager policy；
- input bundle allowed scope 不允许访问 bundle 的父目录或相邻 record bundle；
- `--exact-output-dir` 位于 canonical root 内外时都只保护 current output 本身，不保护无关兄弟目录；
- contamination finding 进入 per-record metadata、archive manifest 和 top-level runtime manifest 的
  现有链路不丢字段。

### 12.3 Trajectory 回归

将最新 RDKit run
`state/benchmark-runs/verifier-grounded-rdkit-qwen3.7-max-20260716-010815` 中触发首轮误判的 tool
call 提取为最小脱敏 fixture，并重放 `audit_attempt()`：

- 合法 current scratch 操作必须为 `clean`；
- 同一 fixture 中把 candidate 替换为真实 `runtime_paths.benchmarks_root` 后必须为
  `contaminated`；
- finding 必须包含 `policy_id`、`resolved_path` 和 `matched_root`；
- 11 题无答案的原始误判路径不得再出现 `verifier_or_gold_path`。

集成验证后，分别对三个 VGB tracks 运行新的 benchmark run；每个 track 必须是独立 run。验证本
规格时只要求 contamination audit 不再因合法 scratch 路径失败，不把模型答题正确率作为本改动的
通过条件。

## 13. 实施步骤

1. 在 `benchmarking/runtime/agent_workspace.py` 增加 `ProtectedRoot`、`PathCandidate`、policy
   normalization 和确定性 root 选择；先用单元测试固定 BFRC-01 至 BFRC-07。
2. 给 `AttemptWorkspaceManager` 增加显式 `protected_roots`，删除 `audit_attempt()` 内部的
   `output_root.parent`、`parents[2]` 和 legacy path 推导。
3. 在 `benchmarking/workflow/cli.py` 增加 production policy builder，直接注入 6.2 和 6.3 的真实
   runtime roots；同步配置预览、judge、compatibility runner 和测试 factories。
4. 重写 candidate extraction：按工具参数来源解析，精确展开 `$VAR` / `${VAR}` / `~`，支持
   `--flag=/path`，返回结构化 resolved candidates。
5. 删除 `_SENSITIVE_RESOURCE_PATTERNS`、`verifier_or_gold_path` 及所有 basename/substring
   forbidden 判断；实现 allowed-first、most-specific protected matching 和 finding 去重。
6. 在 runtime manifest 输出实际 protected policy；确认 archive/per-record findings 保留新字段。
7. 更新 `tests/test_agent_workspace.py`、`tests/test_benchmark_config_runtime.py`、single-LLM、ChemQA、
   judge 和 web-search preflight 相关测试，覆盖第 12 节矩阵。
8. 增加最新 RDKit trajectory 的最小脱敏回归 fixture 并执行 targeted tests，然后执行完整 test
   suite。
9. 实现和测试通过后，更新 `GLOBAL_DEV_SPEC.md`：把当前“boundary-aware VGB resource path
   patterns”描述替换为 root-containment policy，并将本规格状态改为 `DONE`。
10. 按项目流程提交代码、测试、规格状态和 `GLOBAL_DEV_SPEC.md` 的同一完整变更，再开始三个独立
    VGB track benchmark rerun。

## 14. 验收标准

本规格实现完成必须同时满足：

1. 代码中不存在 `_SENSITIVE_RESOURCE_PATTERNS`、`verifier_or_gold_path` 或等价的敏感 basename
   forbidden 规则；
2. 每个 `forbidden_path` finding 都能按 BFRC-01 从 manifest policy 机械验证；
3. allowed-first 行为覆盖 active workspace 和 output 内公开 input bundle；
4. 默认与自定义 benchmark roots 都被保护，名称相同但未配置的目录不被误判；
5. `output_root.parent` 和固定 `parents[n]` 推导已删除；
6. 第 12 节单元、集成和 trajectory regression 全部通过；
7. 最新 RDKit 合法 scratch trajectory 重放为 clean，真实 VGB resource path 重放仍 contaminated；
8. single-LLM skills-on/off、ChemQA、judge 和 web-search preflight 的污染失败语义不变；
9. runtime manifest 可复现本 run 使用的 resolved policy；
10. `GLOBAL_DEV_SPEC.md` 与最终实现一致，相关代码和文档已提交。

## 15. 后续工作

本规格完成后仍存在 transcript audit 固有的动态路径和 syscall 可见性限制。若 benchmark threat
model 提升到主动对抗，应独立设计 filesystem sandbox：只挂载当前 workspace、公开 input bundle
和允许的 skill resources，并在 OS 层拒绝其余路径。该工作不得用继续增加敏感词或 shell 字符串
规则代替。
