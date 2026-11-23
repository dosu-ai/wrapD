#!/usr/bin/env python3
"""Agent Wrap'D: read a year of local coding-agent sessions and score how you run
your agents on five factors (babysitting, quick fixes, verification, where your
rules live, how many agents you run at once), hosted by D.

Usage:
    wrapped.py --scan
    wrapped.py [--sources ID,..] [--projects A,.. | --exclude-projects A,..]
               [--private] [--year YYYY] [--out PATH] [--no-open] [--json]

Prints the output path and a one-line summary so the calling agent spends
almost no tokens.

Privacy, in full:
- Reads: <dir>/projects for each Claude Code config dir (~/.claude,
  ~/.claude-<name>, $CLAUDE_CONFIG_DIR), ~/.codex (or $CODEX_HOME) sessions +
  archived_sessions, ~/.cursor/projects/*/agent-transcripts, and the user name
  from ~/.gitconfig. --sources, --projects and --exclude-projects narrow this;
  --scan prints only counts per source and project.
  To name projects it also checks which folders exist on each session's path
  and reads their .git entry. Source files are never opened. From tool calls it
  keeps the edited file paths (to sort them into code, tests and rule files) and
  matches shell commands against a list of test, lint and type-check tools. From
  tool results it only checks the error flag, the exit code, and whether you
  rejected the call.
- Writes: one HTML file (plus raw stats with --json).
- Never opens a network connection. The only imports are the standard-library
  modules below, and test_wrapped.py enforces that list.
- The only program it starts is your browser, on the output file (skip it with
  --no-open).
- The page itself sets a Content-Security-Policy that blocks every network
  request except Dosu's two fonts from dosu.dev, which carry nothing about you,
  so the browser enforces the same rule.
"""

import argparse
import base64
import json
import os
import re
import statistics
import sys
import webbrowser
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "template.html"
PLACEHOLDER = "__WRAPPED_DATA__"
# Inlined as data URIs, from frontend/marketing/public in dosu-ai/dosu: D sitting (landing/dosu-sitting.png),
# the logomark (hero-new/dosu-icon.svg) and two factory scenes (adx/, scaled down).
ASSETS = {
    "__DOSU_D__": "dosu-d.png",
    "__DOSU_LOGO__": "dosu-logo.svg",
    "__CONVEYOR__": "conveyor.webp",
    "__SKYLINE__": "factory-skyline.webp",
}
MIME = {".png": "image/png", ".svg": "image/svg+xml", ".webp": "image/webp"}
IDLE_CAP = 300  # seconds of silence still counted as active time
# Approximate token counts (word count x ~1.33).
BOOKS = [
    ("English Wikipedia", 6_300_000_000),
    ("In Search of Lost Time", 1_690_000),
    ("the Harry Potter series", 1_440_000),
    ("the King James Bible", 1_040_000),
    ("War and Peace", 780_000),
    ("Moby-Dick", 274_000),
    ("The Great Gatsby", 63_000),
]

# Medians from a sample of hand-driven agent sessions.
TYPICAL = {
    "first": 105,
    "turns_avg": 15.8,
    "turns_med": 4,
    "oneshot": 20,
    "corr": 2.7,
    "ack": 1.6,
    "ping": 0.5,
    "int_sess": 5,
    "q": 22,
    "polite": 4.6,
    "dur": 30,
    "sub_per": 1.4,
    "tools_per": 12.5,
    # Rough guesses, not in that sample: rage prompts and failed tool calls (%),
    # median minutes an agent works between two of your messages, and sessions
    # running at once while you work.
    "rage": 2.0,
    "fail": 8.0,
    "leash": 1.5,
    "parallel": 1.3,
}

TASKS = [  # opening-prompt keyword guess, checked in order
    ("Review", r"\b(review|pr comments?|feedback|lgtm|revisa|revisión|audit)\b"),
    (
        "Debug / fix",
        r"\b(fix|bug|broken|error|fail(s|ing|ed|ure)?|crash|debug|issue|regression|arregla|falla|roto|no funciona)\b",
    ),
    (
        "Docs / writing",
        r"\b(docs?|readme|article|email|blog|copy|write[- ]?up|changelog|escribe|redacta|documenta)\b",
    ),
    (
        "Data / ops / evals",
        r"\b(evals?|data|sql|query|metrics?|dashboard|logs?|logfire|deploy|ci|pipeline|migration|infra|prod|reporte|datos|costos?)\b",
    ),
    (
        "Build",
        r"\b(add|create|implement|build|make|feature|scaffold|new|crea|agrega|añade|implementa|construye|haz)\b",
    ),
]
QUESTION_TASK = "Question / explore"

SESSION_TYPES = [
    ("Spec & ship", "One message of 300+ characters, then done."),
    ("Drive-by", "One or two messages, over in under 15 minutes."),
    (
        "Rescue mission",
        "Four or more turns where a quarter or more are redirects or interrupts.",
    ),
    (
        "Mission control",
        "Runs 4+ hours with 150+ tool calls, or has repeated status check-ins.",
    ),
    ("Long collab build", "Ten or more human turns."),
    ("Quick back-and-forth", "Everything else: a few turns of real conversation."),
]

CORRECTION = re.compile(
    r"^\W*(no\b|nope|actually|instead|wait|stop|don'?t|do not|try again|revert|undo|still (broken|failing|wrong)|that'?s (wrong|not)|"
    r"espera|mejor\b|en vez|en lugar|sigue (sin|fallando|igual|mal)|todav[ií]a|regresa|deshaz|otra vez|eso no|así no|nel\b)",
    re.IGNORECASE,
)
GO_AHEAD = re.compile(
    r"^\W*(y(es|ep|eah)?|ok(ay)?|sure|approved?|continue|go( ahead)?|lgtm|do it|ship it|proceed|sounds good|perfect|great|"
    r"s[ií]|va|dale|adelante|contin[uú]a|hazlo|s[uú]belo|perfecto|listo|correcto|de acuerdo|ándale|sale)\W*(please|por favor|pls)?\W*$",
    re.IGNORECASE,
)
STATUS_PING = re.compile(
    r"^\W*(how'?s it going|progress|status|any update|update\?|c[oó]mo vas|qu[eé] tal vas|ya qued[oó]|avance)\W*\??$",
    re.IGNORECASE,
)
QUESTION = re.compile(
    r"(\?\s*$)|^\W*(why|how|what|where|when|which|who|can|could|is|are|does|do|should|qu[eé]|c[oó]mo|por ?qu[eé]|d[oó]nde|cu[aá]l|se puede)\b",
    re.IGNORECASE,
)
POLITE = re.compile(
    r"\b(please|pls|thanks|thank you|thx|por favor|gracias)\b", re.IGNORECASE
)

RAGE = re.compile(
    r"\b(wtf|wth|ffs|omfg|fuck\w*|shit\w*|damn\w*|dammit|ugh+|seriously|stupid|useless|"
    r"carajo|mierda|ching\w*|verga|pinche|puta|joder)\b|!{2,}|\?{3,}|\?!",
    re.IGNORECASE,
)
REJECTED = re.compile(
    r"doesn't want to (proceed|take this action)|tool use was rejected"
)
EXIT_FAIL = re.compile(
    r'exit_code\\*"\s*:\s*[1-9]|Process exited with code [1-9]|Exit code: [1-9]'
)
CALL_ID = re.compile(r'"call_id":"([^"]+)"')
TIMESTAMP = re.compile(r'"timestamp":"([^"]+)"')
MIN_TOOL_CALLS = 50  # least reliable tool needs at least this many calls

QUICK_FIX = re.compile(
    r"\b(quick(est)? (fix|hack|patch)|smallest (possible )?(change|fix|diff)|minimal (change|fix|diff|patch)|"
    r"simplest (fix|change)|just make (it|them|the tests?) (work|pass)|band-?aid|workaround|monkey-?patch|"
    r"skip (the|this|that) test|arreglo r[aá]pido|parche|cambio m[ií]nimo|lo m[aá]s r[aá]pido)\b",
    re.IGNORECASE,
)
ROOT_CAUSE = re.compile(
    r"\b(root cause|properly|the right way|proper fix|underlying (issue|problem|cause)|fix it for real|"
    r"causa ra[ií]z|de ra[ií]z|bien hecho|como debe ser|la forma correcta)\b",
    re.IGNORECASE,
)
CHECK_CMD = re.compile(
    r"\b(pytest|vitest|jest|mocha|playwright|go (test|vet)|cargo (test|clippy|check)|"
    r"(npm|pnpm|yarn|bun)( run)? (test|lint|types?|typecheck|check)|make (test|lint|check|typecheck)|"
    r"task (test|lint)|tsc|mypy|pyright|ruff|eslint|biome|rubocop|phpunit|unittest|test_\w+\.py)\b"
)
# Agent checking its work at runtime: browser, Playwright, simulator, curl against a local server.
RUNTIME_CMD = re.compile(
    r"playwright|agent-browser|screencapture|curl\s+(-\S+\s+)*\S*(localhost|127\.0\.0\.1)"
)
RUNTIME_TOOL = re.compile(
    r"playwright|browser|preview|chrome|puppeteer|computer|screenshot|simulator",
    re.IGNORECASE,
)
# The trust pyramid (cheapest first): codebase, static analysis, verification,
# prose (rules, skills, AGENTS.md), human review.
PROSE_FILE = re.compile(
    r"(^|/)((AGENTS|CLAUDE|GEMINI|SKILL)\.md|\.cursorrules|\.windsurfrules|copilot-instructions\.md)$"
    r"|\.mdc$|(^|/)\.(claude|cursor)/rules/",
    re.IGNORECASE,
)
GUARD_FILE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$"
    r"|(^|/)(biome\.jsonc?|\.eslintrc[^/]*|eslint\.config\.[^/]+|\.?ruff\.toml|mypy\.ini|tach\.toml"
    r"|\.pre-commit-config\.yaml|tsconfig[^/]*\.json|pyrightconfig\.json|\.golangci\.ya?ml)$",
    re.IGNORECASE,
)
EDIT_TOOLS = {"Edit", "Write", "Patch"}

TAG_BLOCK = re.compile(r"<([a-zA-Z][\w:-]*)\b[^>]*>.*?</\1>", re.DOTALL)
INJECTED_PREFIXES = (
    "This session is being continued",
    "Caveat:",
    "# AGENTS.md instructions",
    "<<autonomous-loop",
    "Stop hook feedback",
    "[Request interrupted",
    "Base directory for this skill",
    "<environment_context",
)
BUILTIN_COMMANDS = {
    "clear",
    "compact",
    "model",
    "exit",
    "quit",
    "resume",
    "login",
    "logout",
    "config",
    "status",
    "cost",
    "help",
    "mcp",
    "context",
    "effort",
    "fast",
    "permissions",
    "plugin",
    "plugins",
    "agents",
    "ide",
    "memory",
    "hooks",
    "doctor",
    "theme",
    "vim",
    "rewind",
    "usage",
    "add-dir",
    "export",
    "release-notes",
    "statusline",
    "bashes",
    "terminal-setup",
    "upgrade",
    "privacy-settings",
    "output-style",
    "sandbox",
    "todos",
    "tasks",
    "init",
}
TOOL_NAMES = {
    "Bash": "Shell",
    "exec_command": "Shell",
    "shell": "Shell",
    "local_shell": "Shell",
    "local_shell_call": "Shell",
    "run_terminal_cmd": "Shell",
    "write_stdin": "Shell input",
    "BashOutput": "Shell input",
    "apply_patch": "Patch",
    "Edit": "Edit",
    "MultiEdit": "Edit",
    "search_replace": "Edit",
    "edit_file": "Edit",
    "Write": "Write",
    "ReadFile": "Read",
    "StrReplace": "Edit",
    "ApplyPatch": "Patch",
    "Read": "Read",
    "read_file": "Read",
    "view_image": "Read",
    "Grep": "Search",
    "Glob": "Search",
    "grep": "Search",
    "codebase_search": "Search",
    "file_search": "Search",
    "Task": "Subagent",
    "Agent": "Subagent",
    "spawn_agent": "Subagent",
    "WebFetch": "Web",
    "WebSearch": "Web",
    "web_search": "Web",
    "web_search_call": "Web",
    "TodoWrite": "Plan",
    "update_plan": "Plan",
    "todo_write": "Plan",
    "ToolSearch": "Tool search",
    "tool_search": "Tool search",
    "tool_search_call": "Tool search",
    "Skill": "Skill",
    "wait": "Wait",
}
PLUMBING = {"Shell input", "Wait", "Tool search", "Plan", "Skill"}
SUBAGENT_TOOLS = {"Task", "Agent", "spawn_agent"}


# ---------- small helpers ----------


def pick_books(tokens):
    """Up to three books read at least once: the biggest, War and Peace, the smallest."""
    fits = [b for b in BOOKS if tokens >= b[1]]
    keep = {b for b in fits if b[0] == "War and Peace"} | set(fits[:1] + fits[-1:])
    for b in fits:
        if len(keep) >= 3:
            break
        keep.add(b)
    return [
        {"title": t, "copies": round(tokens / n)} for t, n in fits if (t, n) in keep
    ]


def abbr(n: float) -> str:
    n = float(n)
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return (
                f"{v:.0f}" if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".")
            ) + suf
    return f"{n:.0f}"


def parse_ts(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def tool_label(name: str) -> str:
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1] if len(parts) > 1 else name
        return "MCP · " + re.sub(r"^claude_ai_", "", server).replace("_", " ")
    return TOOL_NAMES.get(name, name)


_project_cache: dict[tuple, str] = {}


def project_name(cwd, repo_url=None) -> str:
    key = (cwd, repo_url)
    if key in _project_cache:
        return _project_cache[key]
    name = _project_name(cwd, repo_url)
    _project_cache[key] = name
    return name


def _project_name(cwd, repo_url):
    if repo_url:
        base = repo_url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        return base.removesuffix(".git")
    if not cwd:
        return "unknown"
    p = cwd.rstrip("/") or "/"
    q = Path(p)
    for cand in (q, *q.parents):
        if cand == HOME or str(cand) == "/":
            break
        git = cand / ".git"
        try:
            if git.is_dir():
                return cand.name
            if git.is_file():
                gitdir = (
                    git.read_text(errors="replace")
                    .strip()
                    .removeprefix("gitdir:")
                    .strip()
                )
                if "/.git/worktrees/" in gitdir:
                    return os.path.basename(gitdir.split("/.git/worktrees/")[0])
                return cand.name
        except OSError:
            break
    m = re.match(r"(.*?)/\.(?:claude|codex|cursor)/worktrees/", p + "/")
    if m and Path(m.group(1)) != HOME:
        return os.path.basename(m.group(1))
    m = re.search(r"/(?:worktrees|workspaces)/([^/]+)/[^/]+", p)
    if m:
        return m.group(1)
    if q == HOME:
        return "~"
    return q.name or "unknown"


def first_cwd(path: Path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"cwd"' in line:
                try:
                    return json.loads(line).get("cwd")
                except ValueError:
                    continue
    return None


def resolve_dashed(name: str) -> str:
    """Cursor folders flatten a path with dashes; rebuild it by probing the disk."""
    segs = [s for s in name.split("-") if s]
    path = ""
    for seg in segs:
        nxt = f"{path}/{seg}"
        if os.path.exists(nxt) or not path:
            path = nxt
        else:
            joined = f"{path}-{seg}"
            path = joined if os.path.exists(joined) or not os.path.exists(path) else nxt
    return path


def human_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    if "<user_query>" in text:
        text = "\n".join(re.findall(r"<user_query>(.*?)</user_query>", text, re.DOTALL))
    s = text.lstrip()
    if s.startswith(INJECTED_PREFIXES):
        return ""
    for _ in range(2):
        s = TAG_BLOCK.sub("", s)
    s = re.sub(r"\[Image[^\]]*\]", "", s)
    return s.strip()


def classify_task(opening: str) -> str:
    low = opening.lower()
    for name, pat in TASKS:
        if re.search(pat, low):
            return name
    return QUESTION_TASK


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") in ("text", "input_text")
        )
    return ""


def patch_stats(text: str):
    if "*** Begin Patch" not in text:
        return 0, 0, []
    text = text.replace("\\n", "\n")
    added = removed = 0
    files = []
    for ln in text.split("\n"):
        if ln.startswith("*** "):
            m = re.match(r"\*\*\* (?:Add|Update) File: (.+)", ln)
            if m:
                files.append(m.group(1).strip().strip('"'))
        elif ln.startswith("+") and not ln.startswith("+++"):
            added += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            removed += 1
    return added, removed, files


def is_rage(text: str) -> bool:
    head = text[:400]
    letters = [c for c in head if c.isalpha()]
    shouting = len(letters) >= 12 and sum(c.isupper() for c in letters) >= 0.7 * len(
        letters
    )
    return shouting or bool(RAGE.search(head))


def fmt_minutes(m: float) -> str:
    m = round(m)
    return f"{m // 60}h {m % 60}m" if m >= 90 else f"{m} min"


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def nlines(s) -> int:
    return s.count("\n") + 1 if isinstance(s, str) and s else 0


# ---------- the collector ----------


class Session:
    __slots__ = (
        "active",
        "agent",
        "automated",
        "best_run",
        "best_run_at",
        "best_run_tools",
        "branch",
        "checks",
        "cur_run",
        "cwd",
        "edits",
        "end",
        "interrupts",
        "is_sub",
        "last",
        "parent",
        "rejects",
        "repo",
        "run_mark",
        "run_tools",
        "runs",
        "sid",
        "start",
        "subagents",
        "title",
        "tokens",
        "tools",
        "turns",
    )

    def __init__(self, sid, agent):
        self.sid, self.agent = sid, agent
        self.cwd = self.repo = self.title = self.branch = self.parent = None
        self.start = self.end = self.last = None
        self.active = 0.0
        self.tokens = 0
        self.turns: list[str] = []
        self.interrupts = 0
        self.tools = 0
        self.subagents = 0
        self.automated = False
        self.is_sub = False
        self.rejects = 0
        self.best_run = 0.0
        self.best_run_at = None
        self.best_run_tools = 0
        self.run_mark = None  # active seconds when the human last spoke
        self.run_tools = 0
        self.cur_run = 0.0
        self.runs: list[float] = []  # agent-only stretches between human messages
        self.edits = 0
        self.checks = 0

    def end_run(self):
        if self.cur_run:
            self.runs.append(self.cur_run)
        self.cur_run = 0.0

    def touch(self, ts: datetime):
        if self.start is None or ts < self.start:
            self.start = ts
        if self.end is None or ts > self.end:
            self.end = ts
        if self.last is not None:
            gap = (ts - self.last).total_seconds()
            if 0 < gap:
                self.active += min(gap, IDLE_CAP)
        if self.last is None or ts > self.last:
            self.last = ts


class Collector:
    def __init__(self, year: int, keep=None):
        self.year = year
        self.keep = keep  # project-name predicate, None keeps everything
        self.sessions: dict[str, Session] = {}
        self.day_tokens = Counter()
        self.day_project = defaultdict(Counter)
        self.day_sessions = defaultdict(set)
        self.hour_tokens = [0] * 24
        self.hour_turns = [0] * 24
        self.models = Counter()
        self.tools = Counter()
        self.skills = Counter()
        self.agent_tokens = Counter()
        self.p_tokens = Counter()
        self.p_lines = defaultdict(lambda: [0, 0])
        self.p_files = defaultdict(Counter)
        self.p_branches = defaultdict(Counter)
        self.p_models = defaultdict(Counter)
        self.p_days = defaultdict(set)
        self.seen_msgs: set[str] = set()
        self.tool_ids: dict[str, str] = {}
        self.tool_fail = Counter()
        self.first_seen: dict[str, datetime] = {}
        self.layers = Counter()
        self.check_runs = 0
        self.slots = defaultdict(set)  # ten-minute slot -> sessions active in it

    def kept(self, cwd, repo=None):
        return self.keep is None or self.keep(project_name(cwd, repo))

    def project(self, s: Session) -> str:
        parent = self.sessions.get(s.parent) if s.parent else None
        owner = parent if parent and parent.cwd else s
        return project_name(owner.cwd, owner.repo)

    def in_year(self, ts):
        return ts is not None and ts.year == self.year

    def session(self, sid, agent) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            s = self.sessions[sid] = Session(sid, agent)
        return s

    def seen_at(self, s: Session, ts):
        sid = s.parent or s.sid
        self.day_sessions[ts.date().isoformat()].add(sid)
        self.slots[ts.strftime("%Y-%m-%dT%H:") + str(ts.minute // 10)].add(sid)

    def mark_source(self, agent, ts):
        if agent not in self.first_seen or ts < self.first_seen[agent]:
            self.first_seen[agent] = ts

    def add_tokens(self, s: Session, ts, n, model):
        if n <= 0:
            return
        proj = self.project(s)
        d = ts.date().isoformat()
        s.tokens += n
        self.day_tokens[d] += n
        self.day_project[d][proj] += n
        self.hour_tokens[ts.hour] += n
        self.agent_tokens[s.agent] += n
        self.p_tokens[proj] += n
        self.p_days[proj].add(d)
        if model and not model.startswith("<"):
            self.models[model] += n
            self.p_models[proj][model] += n

    def add_turn(self, s: Session, ts, text):
        s.turns.append(text)
        s.end_run()
        s.run_mark, s.run_tools = s.active, 0
        if ts:
            self.hour_turns[ts.hour] += 1

    def agent_busy(self, s: Session, ts):
        """Track the longest stretch of agent activity between two human messages."""
        if s.run_mark is None:
            return
        run = s.active - s.run_mark
        s.cur_run = run
        if run > s.best_run:
            s.best_run, s.best_run_tools, s.best_run_at = run, s.run_tools, ts

    def tool_failed(self, call_id):
        self.tool_fail[self.tool_ids.get(call_id, "?")] += 1

    def add_tool(self, s: Session, name, lines=(0, 0), files=(), cmd=""):
        label = tool_label(name)
        s.tools += 1
        s.run_tools += 1
        self.tools[label] += 1
        if label in EDIT_TOOLS:
            s.edits += 1
        shell = label == "Shell" and cmd
        if shell and CHECK_CMD.search(cmd):
            s.checks += 1
            self.check_runs += 1
        elif (shell and RUNTIME_CMD.search(cmd)) or RUNTIME_TOOL.search(name):
            s.checks += 1
            self.check_runs += 1
            self.layers["verification"] += 1
        if name in SUBAGENT_TOOLS:
            s.subagents += 1
        proj = self.project(s)
        if lines[0] or lines[1]:
            self.p_lines[proj][0] += lines[0]
            self.p_lines[proj][1] += lines[1]
        for f in files:
            if f:
                self.p_files[proj][os.path.basename(f.rstrip("/"))] += 1
                kind = (
                    "prose"
                    if PROSE_FILE.search(f)
                    else "static"
                    if GUARD_FILE.search(f)
                    else "codebase"
                )
                self.layers[kind] += 1

    # ---- Claude Code ----
    def claude(self, root: Path):
        if not root.is_dir():
            return
        for proj_dir in sorted(root.iterdir()):
            if not proj_dir.is_dir():
                continue
            for f in proj_dir.glob("*.jsonl"):
                if self.keep is not None and not self.kept(first_cwd(f)):
                    continue
                s = self.session("claude:" + f.stem, "Claude Code")
                self._claude_file(f, s, sub=False)
                for sf in (proj_dir / f.stem / "subagents").glob("*.jsonl"):
                    sub = self.session(
                        "claude:" + f.stem + "/" + sf.stem, "Claude Code"
                    )
                    sub.is_sub, sub.parent = True, s.sid
                    self._claude_file(sf, sub, sub=True)

    def _claude_file(self, path: Path, s: Session, sub: bool):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"assistant"' in line:
                    pass
                elif '"type":"user"' in line:
                    if '"tool_result"' in line and "Request interrupted" not in line:
                        if '"is_error":true' in line:
                            self._claude_errors(line, s)
                        continue
                elif '"custom-title"' in line[:80] or '"type":"summary"' in line[:80]:
                    pass
                else:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                t = d.get("type")
                if t == "custom-title":
                    s.title = d.get("customTitle") or s.title
                    continue
                if t == "summary":
                    s.title = s.title or d.get("summary")
                    continue
                ts = parse_ts(d.get("timestamp"))
                if not self.in_year(ts):
                    continue
                if d.get("cwd") and not s.cwd:
                    s.cwd = d["cwd"]
                if d.get("gitBranch") and d["gitBranch"] != "HEAD":
                    s.branch = d["gitBranch"]
                if d.get("entrypoint") == "sdk-cli":
                    s.automated = True
                s.touch(ts)
                self.mark_source("Claude Code", ts)
                self.seen_at(s, ts)
                msg = d.get("message") or {}
                if t == "assistant":
                    mid = msg.get("id")
                    u = msg.get("usage")
                    if u and mid not in self.seen_msgs:
                        self.seen_msgs.add(mid)
                        n = sum(
                            u.get(k) or 0
                            for k in (
                                "input_tokens",
                                "cache_creation_input_tokens",
                                "cache_read_input_tokens",
                                "output_tokens",
                            )
                        )
                        self.add_tokens(s, ts, n, msg.get("model"))
                        if s.branch:
                            self.p_branches[self.project(s)][s.branch] += 1
                    for c in msg.get("content") or []:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            self._claude_tool(s, c)
                    self.agent_busy(s, ts)
                elif t == "user" and not d.get("isMeta"):
                    raw = text_of(msg.get("content"))
                    if "Request interrupted by user" in raw:
                        s.interrupts += 1
                        s.end_run()
                        s.run_mark = None
                        continue
                    if sub or d.get("isSidechain"):
                        continue
                    cmd = re.search(r"<command-name>/?([^<\s]+)</command-name>", raw)
                    if cmd:
                        name = cmd.group(1)
                        if name in BUILTIN_COMMANDS:
                            continue
                        self.skills["/" + name] += 1
                        args = re.search(
                            r"<command-args>(.*?)</command-args>", raw, re.DOTALL
                        )
                        raw = args.group(1) if args else ""
                    text = human_text(raw)
                    if text:
                        self.add_turn(s, ts, text)

    def _claude_errors(self, line, s):
        try:
            d = json.loads(line)
        except ValueError:
            return
        if not self.in_year(parse_ts(d.get("timestamp"))):
            return
        for c in (d.get("message") or {}).get("content") or []:
            if (
                isinstance(c, dict)
                and c.get("type") == "tool_result"
                and c.get("is_error")
            ):
                if REJECTED.search(text_of(c.get("content"))):
                    s.rejects += 1
                else:
                    self.tool_failed(c.get("tool_use_id"))

    def _claude_tool(self, s, c):
        name = c.get("name") or "?"
        self.tool_ids[c.get("id")] = tool_label(name)
        inp = c.get("input") or {}
        lines, files = (0, 0), ()
        if name == "Edit":
            lines = (nlines(inp.get("new_string")), nlines(inp.get("old_string")))
            files = (inp.get("file_path"),)
        elif name == "MultiEdit":
            eds = inp.get("edits") or []
            lines = (
                sum(nlines(e.get("new_string")) for e in eds),
                sum(nlines(e.get("old_string")) for e in eds),
            )
            files = (inp.get("file_path"),)
        elif name == "Write":
            lines = (nlines(inp.get("content")), 0)
            files = (inp.get("file_path"),)
        elif name == "Skill" and inp.get("skill"):
            self.skills["/" + str(inp["skill"]).lstrip("/")] += 1
        self.add_tool(s, name, lines, files, str(inp.get("command") or ""))

    # ---- Codex ----
    def codex(self, root: Path):
        files = list((root / "sessions").rglob("*.jsonl")) + list(
            (root / "archived_sessions").glob("*.jsonl")
        )
        for f in sorted(files):
            self._codex_file(f)

    def _codex_file(self, path: Path):
        s = None
        model = None
        prev_total = 0
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                head = line[:320]
                if '"session_meta"' in head:
                    try:
                        p = json.loads(line).get("payload") or {}
                    except ValueError:
                        continue
                    s = self.session("codex:" + str(p.get("id") or path.stem), "Codex")
                    s.cwd = p.get("cwd") or s.cwd
                    git = p.get("git") or {}
                    s.repo = git.get("repository_url") or s.repo
                    if git.get("branch"):
                        s.branch = git["branch"]
                    if not self.kept(s.cwd, s.repo):
                        self.sessions.pop(s.sid, None)
                        return
                    src = p.get("source")
                    if (
                        p.get("parent_thread_id")
                        or p.get("thread_source") == "subagent"
                        or (isinstance(src, dict) and "subagent" in src)
                    ):
                        s.is_sub = True
                        s.parent = (
                            "codex:" + str(p.get("parent_thread_id"))
                            if p.get("parent_thread_id")
                            else None
                        )
                    elif src == "exec" or p.get("originator") == "codex_exec":
                        s.automated = True
                    continue
                if s is None:
                    continue
                kind = None
                if (
                    '"function_call_output"' in head
                    or '"custom_tool_call_output"' in head
                ):
                    m = EXIT_FAIL.search(line)
                    cid = CALL_ID.search(line) if m else None
                    ts = TIMESTAMP.search(line) if cid else None
                    if ts and self.in_year(parse_ts(ts.group(1))):
                        self.tool_failed(cid.group(1))
                    continue
                if '"turn_context"' in head:
                    kind = "ctx"
                elif '"type":"token_count"' in head:
                    kind = "tok"
                elif (
                    '"type":"function_call"' in head
                    or '"type":"custom_tool_call"' in head
                    or '"type":"web_search_call"' in head
                ):
                    kind = "tool"
                elif '"type":"message"' in head and '"role":"user"' in line[:420]:
                    kind = "user"
                elif '"turn_aborted"' in head:
                    kind = "abort"
                if kind is None:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ts = parse_ts(d.get("timestamp"))
                p = d.get("payload") or {}
                if kind == "ctx":
                    model = p.get("model") or model
                    s.cwd = s.cwd or p.get("cwd")
                    continue
                if kind == "tok":
                    tot = ((p.get("info") or {}).get("total_token_usage")) or {}
                    total = tot.get("total_tokens") or 0
                    delta = total - prev_total if total >= prev_total else total
                    prev_total = total
                    if self.in_year(ts) and delta > 0:
                        s.touch(ts)
                        self.mark_source("Codex", ts)
                        self.seen_at(s, ts)
                        self.add_tokens(s, ts, delta, model)
                        self.agent_busy(s, ts)
                    continue
                if not self.in_year(ts):
                    continue
                s.touch(ts)
                self.mark_source("Codex", ts)
                if kind == "abort":
                    if "interrupt" in json.dumps(p).lower():
                        s.interrupts += 1
                        s.end_run()
                        s.run_mark = None
                elif kind == "tool":
                    blob = p.get("input") or p.get("arguments") or ""
                    blob = blob if isinstance(blob, str) else json.dumps(blob)
                    inner = (
                        re.findall(r"tools\.(\w+)\(", blob)
                        if p.get("name") == "exec"
                        else []
                    )
                    names = inner or [p.get("name") or p.get("type")]
                    if p.get("call_id"):
                        self.tool_ids[p["call_id"]] = tool_label(names[0])
                    added, removed, files = patch_stats(blob)
                    for i, n in enumerate(names):
                        self.add_tool(
                            s,
                            n,
                            (added, removed) if i == 0 else (0, 0),
                            files if i == 0 else (),
                            blob,
                        )
                    self.agent_busy(s, ts)
                elif kind == "user" and not s.is_sub:
                    text = human_text(
                        "\n".join(
                            c.get("text", "")
                            for c in p.get("content") or []
                            if isinstance(c, dict)
                            and not c.get("text", "")
                            .lstrip()
                            .startswith(("<", "# AGENTS.md"))
                        )
                    )
                    if text:
                        self.add_turn(s, ts, text)

    # ---- Cursor (turns only: no tokens or per-turn timestamps in its transcripts) ----
    def cursor(self, root: Path):
        if not root.is_dir():
            return
        for f in root.glob("*/agent-transcripts/**/*.jsonl"):
            st = f.stat()
            end = datetime.fromtimestamp(st.st_mtime).astimezone()
            start = datetime.fromtimestamp(
                getattr(st, "st_birthtime", st.st_mtime)
            ).astimezone()
            if not self.in_year(end):
                continue
            cwd = resolve_dashed(f.parts[len(root.parts)])
            if not self.kept(cwd):
                continue
            s = self.session("cursor:" + f.stem, "Cursor")
            s.cwd = cwd
            s.touch(start)
            s.touch(end)
            s.active = min((end - start).total_seconds(), 3600)
            self.mark_source("Cursor", start)
            self.day_sessions[end.date().isoformat()].add(s.sid)
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    content = (d.get("message") or {}).get("content")
                    if d.get("role") == "user":
                        text = human_text(text_of(content))
                        if text:
                            s.turns.append(text)
                    elif d.get("role") == "assistant" and isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                inp = c.get("input") or {}
                                cmd = (
                                    inp.get("command") if isinstance(inp, dict) else ""
                                )
                                self.add_tool(
                                    s, c.get("name") or "?", cmd=str(cmd or "")
                                )

    # ---- summary ----
    def build(self) -> dict:
        subs = [s for s in self.sessions.values() if s.is_sub]
        children = Counter()
        for s in subs:
            parent = self.sessions.get(s.parent) if s.parent else None
            if parent:
                parent.tokens += s.tokens
                parent.tools += s.tools
                parent.edits += s.edits
                parent.checks += s.checks
                children[parent.sid] += 1
        for s in self.sessions.values():
            s.subagents = max(s.subagents, children[s.sid])
            s.end_run()
        mains = [
            s for s in self.sessions.values() if not s.is_sub and s.start is not None
        ]

        openers = Counter(s.turns[0][:200].strip().lower() for s in mains if s.turns)
        for s in mains:
            if s.turns and openers[s.turns[0][:200].strip().lower()] >= 3:
                s.automated = True
        driven = [s for s in mains if s.turns and not s.automated]

        total_tokens = sum(self.day_tokens.values())
        days = sorted(self.day_sessions)
        top = lambda c, n=5: c.most_common(n)

        # biggest day
        biggest = None
        if self.day_tokens:
            d, n = max(self.day_tokens.items(), key=lambda kv: kv[1])
            biggest = {
                "date": d,
                "tokens": n,
                "sessions": len(self.day_sessions[d]),
                "project": self.day_project[d].most_common(1)[0][0],
            }

        # streak
        streak = {"days": 0, "start": None, "end": None}
        run_start, prev = None, None
        for d in days:
            cur = date.fromisoformat(d)
            run_start = (
                cur if prev is None or cur - prev != timedelta(days=1) else run_start
            )
            length = (cur - run_start).days + 1
            if length > streak["days"]:
                streak = {
                    "days": length,
                    "start": run_start.isoformat(),
                    "end": cur.isoformat(),
                }
            prev = cur

        # driving style
        sess_stats = [self._session_style(s) for s in driven]
        stype = Counter(x["type"] for x in sess_stats)
        tasks = Counter(classify_task(s.turns[0]) for s in driven)
        follow = [t for s in driven for t in s.turns[1:]]
        pct = lambda a, b: round(100 * a / b, 1) if b else 0.0
        style = {
            "sessions": len(driven),
            "turns": sum(len(s.turns) for s in driven),
            "first": int(statistics.median(len(s.turns[0]) for s in driven))
            if driven
            else 0,
            "follow": int(statistics.median(len(t) for t in follow)) if follow else 0,
            "turns_med": statistics.median(len(s.turns) for s in driven)
            if driven
            else 0,
            "turns_avg": round(statistics.mean(len(s.turns) for s in driven), 1)
            if driven
            else 0,
            "oneshot": pct(sum(len(s.turns) == 1 for s in driven), len(driven)),
            "corr": pct(sum(bool(CORRECTION.match(t)) for t in follow), len(follow)),
            "ack": pct(sum(bool(GO_AHEAD.match(t)) for t in follow), len(follow)),
            "ping": pct(sum(bool(STATUS_PING.match(t)) for t in follow), len(follow)),
            "int_sess": pct(sum(s.interrupts > 0 for s in driven), len(driven)),
            "interrupts": sum(s.interrupts for s in driven),
            "q": pct(
                sum(bool(QUESTION.search(s.turns[0])) for s in driven), len(driven)
            ),
            "polite": pct(
                sum(bool(POLITE.search(t)) for s in driven for t in s.turns),
                sum(len(s.turns) for s in driven),
            ),
            "dur": round(statistics.median(x["minutes"] for x in sess_stats), 1)
            if sess_stats
            else 0,
            "sub_per": round(sum(s.subagents for s in driven) / len(driven), 2)
            if driven
            else 0,
            "tools_per": round(
                sum(s.tools for s in driven)
                / max(1, sum(len(s.turns) for s in driven)),
                1,
            ),
            "weekend": pct(sum(s.start.weekday() >= 5 for s in driven), len(driven)),
            "night": pct(
                sum(self.hour_tokens[h] for h in (22, 23, 0, 1, 2, 3, 4)), total_tokens
            ),
        }

        rage = sum(is_rage(t) for s in driven for t in s.turns)
        style["rage"] = pct(rage, style["turns"])
        counted = {k: v for k, v in self.tools.items() if k not in PLUMBING}
        fails = {k: self.tool_fail[k] for k in counted}
        style["fail"] = pct(sum(fails.values()), sum(counted.values()))
        flaky = max(
            (k for k, n in counted.items() if n >= MIN_TOOL_CALLS and fails[k]),
            key=lambda k: fails[k] / counted[k],
            default=None,
        )
        runner = max(driven, key=lambda s: s.best_run, default=None)
        agent_side = {
            "longest_run": {
                "minutes": round(runner.best_run / 60),
                "tools": runner.best_run_tools,
                "project": self.project(runner),
                "agent": runner.agent,
                "date": runner.best_run_at.date().isoformat(),
            }
            if runner and runner.best_run
            else None,
            "least_reliable": {
                "name": flaky,
                "calls": counted[flaky],
                "failures": fails[flaky],
                "rate": pct(fails[flaky], counted[flaky]),
            }
            if flaky
            else None,
            "rejections": sum(s.rejects for s in self.sessions.values()),
            "rage": rage,
        }

        projects = []
        for name, n in top(
            Counter(
                {k: v for k, v in self.p_tokens.items() if k not in ("unknown", "~")}
            ),
            8,
        ):
            ps = [s for s in mains if project_name(s.cwd, s.repo) == name]
            pd = sorted(self.p_days[name])
            projects.append(
                {
                    "name": name,
                    "tokens": n,
                    "share": pct(n, total_tokens),
                    "sessions": len(ps),
                    "days": len(pd),
                    "first": pd[0] if pd else None,
                    "last": pd[-1] if pd else None,
                    "lines_added": self.p_lines[name][0],
                    "lines_removed": self.p_lines[name][1],
                    "files": [
                        {"name": f, "edits": c} for f, c in top(self.p_files[name], 4)
                    ],
                    "branches": [b for b, _ in top(self.p_branches[name], 3)],
                    "model": (self.p_models[name].most_common(1) or [(None, 0)])[0][0],
                    "hours": round(sum(s.active for s in ps) / 3600, 1),
                }
            )

        def title(s):
            t = s.title or (s.turns[0] if s.turns else "")
            t = (
                re.sub(r"\s+", " ", re.sub(r"<[^>]*>?", " ", t)).strip()
                or "Untitled session"
            )
            return t[:72] + ("…" if len(t) > 72 else "")

        top_sessions = [
            {
                "title": title(s),
                "project": project_name(s.cwd, s.repo),
                "agent": s.agent,
                "tokens": s.tokens,
                "date": s.start.date().isoformat(),
                "minutes": round(s.active / 60),
                "turns": len(s.turns),
            }
            for s in sorted(mains, key=lambda s: s.tokens, reverse=True)[:5]
            if s.tokens
        ]
        longest = max(mains, key=lambda s: s.active, default=None)

        factors = self._factors(driven, style, agent_side)
        # What you spent on your agents, for the closing card: words you sent, words of
        # context you wrote to open a session, and times you stepped in by hand.
        recap = {
            "agent_hours": round(sum(s.active for s in mains) / 3600),
            "words_sent": sum(len(t.split()) for s in driven for t in s.turns),
            "context_words": sum(len(s.turns[0].split()) for s in driven),
            "stepped_in": self.layers["review"],
            "longest_run_minutes": (agent_side["longest_run"] or {}).get("minutes", 0),
        }
        lines_added = sum(v[0] for v in self.p_lines.values())
        lines_removed = sum(v[1] for v in self.p_lines.values())
        return {
            "year": self.year,
            "sources": [
                {
                    "agent": a,
                    "tokens": self.agent_tokens[a],
                    "since": self.first_seen[a].date().isoformat(),
                    "sessions": sum(1 for s in mains if s.agent == a),
                }
                for a in sorted(self.first_seen)
            ],
            "totals": {
                "tokens": total_tokens,
                "sessions": len(mains),
                "driven": len(driven),
                "automated": sum(1 for s in mains if s.automated),
                "subagents": sum(s.subagents for s in mains)
                + sum(1 for s in subs if s.parent not in self.sessions),
                "prompts": sum(len(s.turns) for s in mains),
                "tool_calls": sum(self.tools.values()),
                "lines_added": lines_added,
                "lines_removed": lines_removed,
                "active_days": len(days),
                "agent_hours": round(sum(s.active for s in mains) / 3600, 1),
                "projects": len(self.p_tokens),
            },
            "books": pick_books(total_tokens),
            "biggest_day": biggest,
            "streak": streak,
            "longest_session": {
                "title": title(longest),
                "minutes": round(longest.active / 60),
                "project": project_name(longest.cwd, longest.repo),
                "agent": longest.agent,
            }
            if longest
            else None,
            "hours": {"tokens": self.hour_tokens, "turns": self.hour_turns},
            "models": [
                {"name": m, "tokens": n, "share": pct(n, total_tokens)}
                for m, n in top(self.models)
            ],
            "tools": [
                {"name": t, "calls": n}
                for t, n in top(
                    Counter({k: v for k, v in self.tools.items() if k not in PLUMBING})
                )
            ],
            "skills": [{"name": k, "uses": n} for k, n in top(self.skills)],
            "genres": [
                {"name": k, "sessions": n, "share": pct(n, len(driven))}
                for k, n in tasks.most_common()
            ],
            "session_types": [
                {
                    "name": k,
                    "desc": desc,
                    "sessions": stype[k],
                    "share": pct(stype[k], len(driven)),
                }
                for k, desc in SESSION_TYPES
                if stype[k]
            ],
            "style": style,
            "typical": TYPICAL,
            "agent_side": agent_side,
            "layers": {
                k: self.layers[k]
                for k in ("codebase", "static", "verification", "prose", "review")
            },
            "factors": factors,
            "profile": profile(factors, recap),
            "recap": recap,
            "projects": projects,
            "top_sessions": top_sessions,
        }

    def _factors(self, driven, style, side) -> list:
        g = lambda k: style.get(k) or 0

        def rel(k, value=None):
            return min(4.0, (g(k) if value is None else value) / (TYPICAL.get(k) or 1))

        def lean(r):
            return r / (1 + r)

        runs = [r for s in driven for r in s.runs]
        leash = round(statistics.median(runs) / 60, 1) if runs else 0.0
        hover = statistics.mean([rel("int_sess"), rel("ping"), rel("corr")])
        longest = (side.get("longest_run") or {}).get("minutes", 0)

        turns = [t for s in driven for t in s.turns]
        quick = sum(bool(QUICK_FIX.search(t)) for t in turns)
        root = sum(bool(ROOT_CAUSE.search(t)) for t in turns)

        edited = [s for s in driven if s.edits]
        checked = sum(1 for s in edited if s.checks)
        corrections = sum(
            bool(CORRECTION.match(t)) for s in driven for t in s.turns[1:]
        )
        self.layers["review"] = (
            corrections + g("interrupts") + (side.get("rejections") or 0)
        )
        static, prose, review = (
            self.layers["static"],
            self.layers["prose"],
            self.layers["review"],
        )

        sizes = [len(v) for v in self.slots.values()]
        peak = max(sizes, default=0)
        busy = statistics.mean(sizes) if sizes else 0.0
        subagents = sum(s.subagents for s in driven)

        score = {
            "babysit": lean(rel("leash", leash) / max(0.25, hover)),
            "quick": (root + 1) / (quick + root + 2),
            "verify": checked / len(edited) if edited else 0.5,
            "trust": (static + 1) / (static + prose + review + 2),
            "line": lean(statistics.mean([rel("parallel", busy), rel("sub_per")])),
        }
        facts = {
            "babysit": {
                "hero": fmt_minutes(longest),
                "label": "the longest one of your agents worked without hearing from you",
                "leash": leash,
                "evidence": [
                    f"Half the time you spoke up again within {fmt_minutes(leash)} of agent work.",
                    f"{g('int_sess')}% of your sessions had an interrupt.",
                    f"You turned down {plural(side.get('rejections') or 0, 'tool call')}.",
                ],
            },
            "quick": {
                "hero": f"{quick:,}",
                "label": "messages asking for a quick fix, the smallest change, or a workaround",
                "count": quick,
                "root": root,
                "evidence": [
                    f"{plural(root, 'message')} asked for the root cause or the proper fix.",
                    f"Quick-fix asks were {round(100 * quick / len(turns), 1) if turns else 0}% of everything you typed.",
                ],
            },
            "verify": {
                "hero": f"{round(100 * score['verify'])}%" if edited else "-",
                "label": "of your sessions that changed code also ran tests, lint or a type check",
                "evidence": [
                    f"{plural(self.check_runs, 'check run')} across {plural(len(edited), 'session')} that changed code.",
                ],
            },
            "trust": {
                "hero": f"{static:,}",
                "label": "edits to tests and lint or type config",
                "evidence": [
                    f"{plural(prose, 'edit')} to AGENTS.md, skills and rule files.",
                    f"{plural(review, 'time')} you stepped in yourself: a correction, an interrupt or a rejected tool call.",
                ],
            },
            "line": {
                "hero": f"{peak:,}",
                "label": "sessions running in the same ten minutes, at your busiest",
                "peak": peak,
                "evidence": [
                    f"On average {busy:.1f} sessions were running whenever you were working.",
                    f"Your sessions launched {plural(subagents, 'subagent')}.",
                ],
            },
        }
        return [
            {
                "key": k,
                "name": n,
                "question": q,
                "low": lo,
                "high": hi,
                "score": round(100 * score[k]),
                **facts[k],
            }
            for k, n, q, lo, hi in FACTORS
        ]

    @staticmethod
    def _session_style(s: Session) -> dict:
        turns = s.turns
        minutes = (s.end - s.start).total_seconds() / 60
        redirects = sum(bool(CORRECTION.match(t)) for t in turns[1:]) + s.interrupts
        pings = sum(bool(STATUS_PING.match(t)) for t in turns[1:])
        if len(turns) == 1 and len(turns[0]) >= 300:
            kind = "Spec & ship"
        elif len(turns) <= 2 and minutes < 15:
            kind = "Drive-by"
        elif len(turns) >= 4 and redirects / len(turns) >= 0.25:
            kind = "Rescue mission"
        elif (minutes >= 240 and s.tools >= 150) or pings >= 2:
            kind = "Mission control"
        elif len(turns) >= 10:
            kind = "Long collab build"
        else:
            kind = "Quick back-and-forth"
        return {"type": kind, "minutes": minutes}


# ---------- the factory floor: five factors and the profile read ----------

FACTORS = [  # key, name, question, low end, high end
    ("babysit", "Babysitting", "Do you babysit your agents?", "Hovering", "Hands-off"),
    ("quick", "Quick fixes", "Do you ask for the quick fix?", "Band-aid", "Root cause"),
    ("verify", "Verification", "Do your agents prove it works?", "Ship it", "Prove it"),
    ("trust", "Trust", "Where does your trust come from?", "Say it again", "Encode it"),
    (
        "line",
        "The line",
        "How many agents do you run at once?",
        "One bench",
        "Full line",
    ),
]
ARCHETYPES = {  # (factor, leans high) -> name, one line
    ("babysit", False): (
        "Shift Supervisor",
        "I stand next to the line and watch every weld",
    ),
    ("babysit", True): ("Night Shift Manager", "I start the line and go home"),
    ("quick", False): ("Duct Tape Specialist", "if it holds, it ships"),
    ("quick", True): ("Root Cause Analyst", "I don't patch what I don't understand"),
    ("verify", False): ("Express Shipper", "my agents say done and I believe them"),
    ("verify", True): ("Quality Inspector", "nothing leaves my floor untested"),
    ("trust", False): ("Broken Record", "I say it again until it sticks"),
    ("trust", True): ("Toolmaker", "I build jigs so the line can't get it wrong"),
    ("line", False): ("Craftsperson", "one bench, one agent, full attention"),
    ("line", True): ("Plant Manager", "I run more lines than I can watch"),
}
TIPS = {
    "babysit": "Give one agent a clear goal and a way to check its work, then leave it alone for twenty minutes.",
    "quick": "Next time something breaks, ask for the root cause. The next agent copies whatever fix you accept.",
    "verify": "Make your agents run the tests before they tell you they're done.",
    "trust": "Take the correction you repeat most and turn it into a lint rule or a test.",
    "line": "Try two agents at once, each in its own worktree.",
}


CARD = ("agent_hours", "longest_run_minutes", "stepped_in", "context_words")


def profile(factors: list, recap: dict) -> dict:
    """Name the strongest lean, and point the weakest factor at something to try."""
    strongest = max(factors, key=lambda f: abs(f["score"] - 50))
    weakest = min(factors, key=lambda f: f["score"])
    name, line = ARCHETYPES[(strongest["key"], strongest["score"] >= 50)]
    article = "an" if name[0] in "AEIOU" else "a"
    # dosu.dev/wrapd redraws the closing card (and its preview image) from these two params.
    slug = name.lower().replace(" ", "-")
    url = f"https://dosu.dev/wrapd?a={slug}&n=" + ".".join(
        str(int(recap[k])) for k in CARD
    )
    return {
        "name": name,
        "line": line,
        "factor": strongest["key"],
        "try": TIPS[weakest["key"]],
        "url": url,
        "share": f"I'm {article} {name}: {line}. What's yours? {url}",
    }


def discover_sources(home: Path = HOME) -> list:
    """Every local log folder found: one entry per Claude config dir (~/.claude,
    ~/.claude-<name>, $CLAUDE_CONFIG_DIR), plus Codex and Cursor."""
    claude_dirs = [
        home / ".claude",
        *sorted(d for d in home.glob(".claude-*") if d.is_dir()),
    ]
    if home == HOME and os.environ.get("CLAUDE_CONFIG_DIR"):
        claude_dirs.append(Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser())
    codex = (
        Path(os.environ.get("CODEX_HOME") or home / ".codex")
        if home == HOME
        else home / ".codex"
    )
    found, ids = [], set()

    def add(kind, path, name):
        if any(x["path"] == path for x in found):
            return
        sid, n = name, 2
        while sid in ids:
            sid, n = f"{name}-{n}", n + 1
        ids.add(sid)
        found.append({"id": sid, "kind": kind, "path": path})

    for d in claude_dirs:
        if (d / "projects").is_dir():
            add("claude", d / "projects", d.name.lstrip(".") or "claude")
    if (codex / "sessions").is_dir() or (codex / "archived_sessions").is_dir():
        add("codex", codex, "codex")
    if (home / ".cursor" / "projects").is_dir():
        add("cursor", home / ".cursor" / "projects", "cursor")
    return found


def collect_sources(sources, year, projects=None, exclude=None) -> dict:
    only = {x.lower() for x in projects or ()}
    skip = {x.lower() for x in exclude or ()}
    keep = None
    if only or skip:
        keep = lambda name: (
            (not only or name.lower() in only) and name.lower() not in skip
        )
    c = Collector(year, keep)
    for src in sources:
        getattr(c, src["kind"])(Path(src["path"]))
    return c.build()


def collect(claude_dir, codex_dir, year, cursor_dir=None) -> dict:
    sources = [
        {"kind": "claude", "path": claude_dir},
        {"kind": "codex", "path": codex_dir},
    ]
    if cursor_dir:
        sources.append({"kind": "cursor", "path": cursor_dir})
    return collect_sources(sources, year)


def redact(data: dict) -> dict:
    """--private: drop session titles, file names and branches, which can quote
    prompts or reveal work in progress if the story is shared."""
    data = json.loads(json.dumps(data))
    data["private"] = True
    for x in data.get("top_sessions") or []:
        h, m = divmod(x.get("minutes") or 0, 60)
        x["title"] = (
            f"{x.get('turns', 0)} prompts over {f'{h}h {m}m' if h else f'{m}m'}"
        )
    if data.get("longest_session"):
        data["longest_session"]["title"] = ""
    for p in data.get("projects") or []:
        p["files"], p["branches"] = [], []
    return data


def scan_text(sources, year) -> str:
    """Inventory for choosing scope: counts per source and project, no content."""
    lines = [f"Sources found for {year} (pick with --sources):"]
    projects = defaultdict(lambda: [0, 0])
    for src in sources:
        d = collect_sources([src], year)
        t = d["totals"]
        since = min((x["since"] for x in d["sources"]), default="-")
        lines.append(
            f"  {src['id']:<18} {str(src['path']).replace(str(HOME), '~'):<28} "
            f"{t['sessions']:>4} sessions  {abbr(t['tokens']):>6} tokens  since {since}"
        )
        for p in d["projects"]:
            projects[p["name"]][0] += p["sessions"]
            projects[p["name"]][1] += p["tokens"]
    lines.append("Projects (pick with --projects or --exclude-projects):")
    for name, (n, tok) in sorted(projects.items(), key=lambda kv: -kv[1][1])[:15]:
        lines.append(
            f"  {name:<24} {n:>4} session{'' if n == 1 else 's'}  {abbr(tok):>6} tokens"
        )
    if len(projects) > 15:
        lines.append(f"  ...and {len(projects) - 15} more")
    return "\n".join(lines)


def render(data: dict, template: Path = TEMPLATE) -> str:
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")
    html = Path(template).read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"template is missing {PLACEHOLDER}")
    for key, name in ASSETS.items():
        path = TEMPLATE.with_name(name)
        blob64 = base64.b64encode(path.read_bytes()).decode()
        html = html.replace(key, f"data:{MIME[path.suffix]};base64,{blob64}")
    return html.replace(PLACEHOLDER, blob, 1)


def first_name() -> str:
    try:
        cfg = (HOME / ".gitconfig").read_text(encoding="utf-8", errors="replace")
    except OSError:
        cfg = ""
    m = re.search(r"^\s*name\s*=\s*(.+)$", cfg, re.MULTILINE)
    return ((m.group(1) if m else "").split() or [os.environ.get("USER", "there")])[0]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    csv = lambda v: [x.strip() for x in v.split(",") if x.strip()]
    ap.add_argument(
        "--year", type=int, default=datetime.now(timezone.utc).astimezone().year
    )
    ap.add_argument(
        "--scan",
        action="store_true",
        help="list sources and projects with counts only, then exit",
    )
    ap.add_argument(
        "--sources",
        type=csv,
        help="comma-separated source ids from --scan (default: all)",
    )
    ap.add_argument(
        "--projects", type=csv, help="only these projects (comma-separated)"
    )
    ap.add_argument(
        "--exclude-projects", type=csv, help="skip these projects (comma-separated)"
    )
    ap.add_argument(
        "--private",
        action="store_true",
        help="hide session titles, file names and branches",
    )
    ap.add_argument(
        "--out",
        help="output HTML path (default ~/Downloads/agent-wrapd-<year>.html)",
    )
    ap.add_argument(
        "--name", help="name for the greeting (default: first word of git user.name)"
    )
    ap.add_argument(
        "--json", action="store_true", help="also write the raw stats next to the HTML"
    )
    ap.add_argument(
        "--no-open", action="store_true", help="don't open the result in a browser"
    )
    args = ap.parse_args(argv)

    sources = discover_sources()
    if args.sources:
        unknown = set(args.sources) - {x["id"] for x in sources}
        if unknown:
            sys.exit(
                f"Unknown source {', '.join(sorted(unknown))}. Found: {', '.join(x['id'] for x in sources)}"
            )
        sources = [x for x in sources if x["id"] in args.sources]
    if args.scan:
        print(scan_text(sources, args.year))
        return
    data = collect_sources(sources, args.year, args.projects, args.exclude_projects)
    if not data["totals"]["sessions"]:
        sys.exit(f"No agent sessions found for {args.year}.")
    if args.private:
        data = redact(data)
    name = args.name or first_name()
    data["name"] = name[:1].upper() + name[1:]
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    downloads = HOME / "Downloads"
    out = Path(
        args.out
        or (downloads if downloads.is_dir() else HOME) / f"agent-wrapd-{args.year}.html"
    ).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    if args.json:
        out.with_suffix(".json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    t = data["totals"]
    top_project = data["projects"][0]["name"] if data["projects"] else "-"
    print(out)
    print(
        f"{abbr(t['tokens'])} tokens · {t['sessions']} sessions · {t['prompts']} prompts · "
        f"top project {top_project} · {data['profile']['name']}"
    )
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
