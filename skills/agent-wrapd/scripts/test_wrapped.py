#!/usr/bin/env python3
"""Tests for wrapped.py: builds tiny Claude Code and Codex logs, runs the
collector and the template render, and checks the outcomes.

Run directly; exits nonzero on failure:

    python3 skills/agent-wrapd/scripts/test_wrapped.py

Dependency-free (no pytest).
"""

import ast
import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "wrapped", Path(__file__).with_name("wrapped.py")
)
wrapped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapped)

failures = []


def check(name, actual, expected):
    if actual != expected:
        failures.append(f"{name}: expected {expected!r}, got {actual!r}")


def jl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    )


def claude_fixture(root):
    base = {"cwd": "/nowhere/acme", "sessionId": "s1", "gitBranch": "feat-login"}
    usage = {
        "input_tokens": 10,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 1000,
        "output_tokens": 50,
    }
    edit = {
        "type": "tool_use",
        "name": "Edit",
        "input": {
            "file_path": "/nowhere/acme/src/app.py",
            "old_string": "a",
            "new_string": "b\nc",
        },
    }
    bash = {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
    jl(
        root / "-nowhere-acme" / "s1.jsonl",
        [
            {"type": "custom-title", "customTitle": "Fix login", "sessionId": "s1"},
            {
                **base,
                "type": "user",
                "timestamp": "2026-03-02T23:00:00Z",
                "message": {"role": "user", "content": "fix the failing login test"},
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-03-02T23:01:00Z",
                "message": {
                    "id": "m1",
                    "model": "claude-opus-5-5",
                    "usage": usage,
                    "content": [edit],
                },
            },
            # Same message id logged again for its next content block: tokens count once.
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-03-02T23:01:00Z",
                "message": {
                    "id": "m1",
                    "model": "claude-opus-5-5",
                    "usage": usage,
                    "content": [bash],
                },
            },
            {
                **base,
                "type": "user",
                "timestamp": "2025-12-31T10:00:00Z",
                "message": {"role": "user", "content": "last year"},
            },
            {
                **base,
                "type": "user",
                "timestamp": "2026-03-02T23:02:00Z",
                "message": {
                    "role": "user",
                    "content": "<command-name>/code-review</command-name>",
                },
            },
        ],
    )
    jl(
        root / "-nowhere-acme" / "s1" / "subagents" / "agent-1.jsonl",
        [
            # Isolated-worktree subagent: its own cwd, but its work belongs to the parent's project.
            {
                **base,
                "cwd": "/gone/worktrees/agent-zz",
                "type": "assistant",
                "isSidechain": True,
                "timestamp": "2026-03-02T23:03:00Z",
                "message": {
                    "id": "m2",
                    "model": "claude-sonnet-5-5",
                    "usage": {"input_tokens": 500, "output_tokens": 0},
                    "content": [],
                },
            },
        ],
    )


def codex_fixture(root):
    patch = "*** Begin Patch\n*** Add File: web/theme.ts\n+one\n+two\n*** End Patch"
    jl(
        root / "sessions" / "2026" / "03" / "03" / "rollout-x.jsonl",
        [
            {
                "timestamp": "2026-03-03T15:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "c1",
                    "cwd": "/nowhere/widget",
                    "git": {
                        "repository_url": "git@github.com:org/widget.git",
                        "branch": "main",
                    },
                },
            },
            {
                "timestamp": "2026-03-03T15:00:01Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5", "cwd": "/nowhere/widget"},
            },
            {
                "timestamp": "2026-03-03T15:00:02Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "<environment_context>x</environment_context>",
                        },
                        {"type": "input_text", "text": "add a dark mode toggle"},
                    ],
                },
            },
            {
                "timestamp": "2026-03-03T15:00:03Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": "{}",
                },
            },
            {
                "timestamp": "2026-03-03T15:00:04Z",
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "apply_patch",
                    "input": patch,
                },
            },
            {
                "timestamp": "2026-03-03T15:00:05Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 900,
                            "output_tokens": 100,
                            "total_tokens": 1000,
                        }
                    },
                },
            },
            {
                "timestamp": "2026-03-03T15:00:06Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 2700,
                            "output_tokens": 300,
                            "total_tokens": 3000,
                        }
                    },
                },
            },
        ],
    )


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    claude_fixture(tmp / "claude")
    codex_fixture(tmp / "codex")
    data = wrapped.collect(tmp / "claude", tmp / "codex", 2026)
    t = data["totals"]
    check(
        "total tokens (deduped, year-filtered, subagent included)",
        t["tokens"],
        1160 + 500 + 3000,
    )
    check("main sessions", t["sessions"], 2)
    check("subagents", t["subagents"], 1)
    check("prompts", t["prompts"], 2)
    check("lines added", t["lines_added"], 4)
    check("top model", data["models"][0]["name"], "gpt-5.5")
    check("projects", sorted(p["name"] for p in data["projects"]), ["acme", "widget"])
    check("top project", data["projects"][0]["name"], "widget")
    acme = next(p for p in data["projects"] if p["name"] == "acme")
    check("acme tokens include its subagent", acme["tokens"], 1660)
    check("acme edited file", acme["files"][0]["name"], "app.py")
    check("acme branch", acme["branches"][0], "feat-login")
    check(
        "session title",
        next(s for s in data["top_sessions"] if s["project"] == "acme")["title"],
        "Fix login",
    )
    check("genres", sorted(g["name"] for g in data["genres"]), ["Build", "Debug / fix"])
    check("skills", data["skills"][0]["name"], "/code-review")
    check(
        "tools", {x["name"] for x in data["tools"]} >= {"Shell", "Edit", "Patch"}, True
    )
    check("active days", t["active_days"], 2)

    html = wrapped.render(
        {**data, "name": "</script><b>"},
        Path(__file__).parents[1] / "assets" / "template.html",
    )
    check("placeholders replaced", re.findall(r"__[A-Z_]+__", html), [])
    check("mascot inlined as data URI", "data:image/png;base64,iVBOR" in html, True)
    blob = re.search(
        r'<script id="wrapped-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    check(
        "data script closes once",
        blob is not None and json.loads(blob.group(1))["name"],
        "</script><b>",
    )


# Sources and scope: a second Claude config dir, project filters, private mode, scan.
def personal_fixture(root):
    base = {"cwd": "/nowhere/garden", "sessionId": "p1"}
    jl(
        root / "-nowhere-garden" / "p1.jsonl",
        [
            {
                **base,
                "type": "user",
                "timestamp": "2026-04-01T10:00:00Z",
                "message": {"role": "user", "content": "plan the garden beds"},
            },
            {
                **base,
                "type": "assistant",
                "timestamp": "2026-04-01T10:01:00Z",
                "message": {
                    "id": "g1",
                    "model": "claude-opus-5-5",
                    "usage": {"input_tokens": 7000},
                    "content": [],
                },
            },
        ],
    )


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    claude_fixture(home / ".claude" / "projects")
    personal_fixture(home / ".claude-personal" / "projects")
    codex_fixture(home / ".codex")
    (home / ".claude-empty").mkdir()
    found = wrapped.discover_sources(home)
    check(
        "discovered sources",
        [x["id"] for x in found],
        ["claude", "claude-personal", "codex"],
    )

    every = wrapped.collect_sources(found, 2026)
    check(
        "all sources",
        sorted(p["name"] for p in every["projects"]),
        ["acme", "garden", "widget"],
    )
    work = wrapped.collect_sources(
        [x for x in found if x["id"] != "claude-personal"], 2026
    )
    check(
        "one source left out",
        sorted(p["name"] for p in work["projects"]),
        ["acme", "widget"],
    )
    check("left-out source adds no tokens", work["totals"]["tokens"], 4660)

    only = wrapped.collect_sources(found, 2026, projects={"widget"})
    check(
        "--projects keeps only those", [p["name"] for p in only["projects"]], ["widget"]
    )
    check("--projects tokens", only["totals"]["tokens"], 3000)
    skip = wrapped.collect_sources(found, 2026, exclude={"widget", "garden"})
    check("--exclude-projects", [p["name"] for p in skip["projects"]], ["acme"])
    check("--exclude-projects drops its sessions", skip["totals"]["sessions"], 1)

    hidden = wrapped.redact(every)
    titles = [x["title"] for x in hidden["top_sessions"]] + [
        hidden["longest_session"]["title"]
    ]
    check(
        "private hides session titles",
        [t for t in titles if "garden beds" in t or "Fix login" in t],
        [],
    )
    check(
        "private hides file names",
        [p["files"] for p in hidden["projects"]],
        [[], [], []],
    )
    check(
        "private hides branches",
        [p["branches"] for p in hidden["projects"]],
        [[], [], []],
    )

    scan = wrapped.scan_text(found, 2026)
    check("scan lists each source", all(x["id"] in scan for x in found), True)
    check(
        "scan lists projects",
        all(n in scan for n in ("acme", "garden", "widget")),
        True,
    )
    check(
        "scan shows no prompt text",
        "garden beds" in scan or "login test" in scan,
        False,
    )


# Wrap'D: the agent's side (unattended runs, failing tools, rejections) and the personality result.
def moody_fixture(root):
    base = {"cwd": "/nowhere/moody", "sessionId": "r1"}
    usage = {"input_tokens": 10, "output_tokens": 1}

    def at(minute):
        return f"2026-05-04T{10 + minute // 60:02d}:{minute % 60:02d}:00Z"

    def ask(minute, text):
        return {
            **base,
            "type": "user",
            "timestamp": at(minute),
            "message": {"role": "user", "content": text},
        }

    def call(minute, mid, tool, tid):
        return {
            **base,
            "type": "assistant",
            "timestamp": at(minute),
            "message": {
                "id": mid,
                "model": "claude-opus-5-5",
                "usage": usage,
                "content": [{"type": "tool_use", "id": tid, "name": tool, "input": {}}],
            },
        }

    def result(minute, tid, error, text="Exit code 1"):
        return {
            **base,
            "type": "user",
            "timestamp": at(minute),
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "is_error": error,
                        "content": text,
                    }
                ],
            },
        }

    rows = [ask(0, "please build the export page")]
    for i in range(48):  # 48 Bash calls a minute apart, every fourth one fails
        rows += [
            call(1 + i, f"m{i}", "Bash", f"b{i}"),
            result(1 + i, f"b{i}", i % 4 == 0),
        ]
    rows += [
        call(49, "e1", "Edit", "e1"),
        result(
            50,
            "e1",
            True,
            "The user doesn't want to proceed with this tool use. The tool use was rejected.",
        ),
        ask(64, "WHY IS THIS STILL BROKEN"),
        ask(65, "ok fine, thanks"),
        call(66, "m99", "Read", "r1"),
    ]
    jl(root / "-nowhere-moody" / "r1.jsonl", rows)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    moody_fixture(tmp / "claude")
    codex_fixture(tmp / "codex")
    out = tmp / "codex" / "sessions" / "2026" / "03" / "03" / "rollout-x.jsonl"
    out.write_text(
        out.read_text()
        + "".join(
            json.dumps(r, separators=(",", ":")) + "\n"
            for r in [
                {
                    "timestamp": "2026-03-03T15:00:07Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": "k1",
                        "arguments": "{}",
                    },
                },
                {
                    "timestamp": "2026-03-03T15:00:08Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "k1",
                        "output": "Process exited with code 2\nnope",
                    },
                },
            ]
        )
    )
    moody = wrapped.collect(tmp / "claude", tmp / "codex", 2026)
    side = moody["agent_side"]
    check(
        "longest unattended run (5-minute idle cap)", side["longest_run"]["minutes"], 49
    )
    check("tool calls in that run", side["longest_run"]["tools"], 49)
    check("longest run project", side["longest_run"]["project"], "moody")
    check("least reliable tool", side["least_reliable"]["name"], "Shell")
    check(
        "least reliable failures (Claude is_error + Codex exit code)",
        side["least_reliable"]["failures"],
        13,
    )
    check("least reliable calls", side["least_reliable"]["calls"], 50)
    check("rejected tool calls are not failures", side["rejections"], 1)
    check("rage prompts", side["rage"], 1)
    check("rage share of messages", moody["style"]["rage"], round(100 / 4, 1))


# Factory floor: the five factors and the profile read built from them.
def factory_fixture(root):
    def row(sid, minute, kind, content, mid=None):
        r = {
            "cwd": "/nowhere/plant",
            "sessionId": sid,
            "type": kind,
            "timestamp": f"2026-06-01T09:{minute:02d}:00Z",
            "message": {"role": kind, "content": content},
        }
        if mid:
            r["message"].update(
                id=mid, model="claude-opus-5-5", usage={"input_tokens": 5}
            )
        return r

    def tool(name, **inp):
        return [
            {"type": "tool_use", "id": name + str(len(inp)), "name": name, "input": inp}
        ]

    jl(
        root / "-nowhere-plant" / "f1.jsonl",
        [
            row("f1", 0, "user", "just give me a quick fix for the flaky login test"),
            row(
                "f1",
                1,
                "assistant",
                tool(
                    "Edit",
                    file_path="/nowhere/plant/AGENTS.md",
                    old_string="a",
                    new_string="b",
                ),
                "a1",
            ),
            row(
                "f1",
                2,
                "assistant",
                tool(
                    "Edit",
                    file_path="/nowhere/plant/tests/test_login.py",
                    old_string="a",
                    new_string="b",
                ),
                "a2",
            ),
            row(
                "f1",
                3,
                "assistant",
                tool(
                    "Edit",
                    file_path="/nowhere/plant/src/login.py",
                    old_string="a",
                    new_string="b",
                ),
                "a3",
            ),
            row(
                "f1",
                4,
                "assistant",
                tool("Bash", command="pnpm test --filter login"),
                "a4",
            ),
            row(
                "f1",
                5,
                "assistant",
                tool("Bash", command="curl -s localhost:3000/health"),
                "a6",
            ),
            row("f1", 6, "user", "no, revert that"),
            row("f1", 9, "user", "ok now find the root cause"),
            row("f1", 10, "assistant", tool("Bash", command="ls"), "a5"),
        ],
    )
    jl(
        root / "-nowhere-plant" / "f2.jsonl",
        [
            row("f2", 2, "user", "add a csv export"),
            row(
                "f2",
                3,
                "assistant",
                tool("Write", file_path="/nowhere/plant/src/export.py", content="x"),
                "b1",
            ),
        ],
    )


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    factory_fixture(tmp / "claude")
    plant = wrapped.collect(tmp / "claude", tmp / "nope", 2026)
    f = {x["key"]: x for x in plant["factors"]}
    check(
        "five factors in order",
        list(f),
        ["babysit", "quick", "verify", "trust", "line"],
    )
    check("scores are 0-100", all(0 <= x["score"] <= 100 for x in f.values()), True)
    check(
        "edits land on the trust pyramid",
        plant["layers"],
        {"codebase": 2, "static": 1, "verification": 1, "prose": 1, "review": 1},
    )
    check("quick-fix asks", (f["quick"]["count"], f["quick"]["root"]), (1, 1))
    check("one of two editing sessions ran a check", f["verify"]["score"], 50)
    check("two sessions in the same ten minutes", f["line"]["peak"], 2)
    check("median unattended stretch, in minutes", f["babysit"]["leash"], 1.0)
    check("evidence is written out", all(x["evidence"] for x in f.values()), True)
    check(
        "recap numbers for the last card",
        plant["recap"],
        {
            "agent_hours": 0,
            "words_sent": 24,
            "context_words": 15,
            "stepped_in": 1,
            "longest_run_minutes": 5,
        },
    )
    p = plant["profile"]
    check(
        "profile names an archetype",
        p["name"] in {a[0] for a in wrapped.ARCHETYPES.values()},
        True,
    )
    check(
        "share link carries the result",
        bool(
            re.fullmatch(
                r"https://dosu\.dev/wrapd\?a=[a-z-]+&n=\d+(\.\d+){3}", p["url"]
            )
        ),
        True,
    )
    check("share text ends with the link", p["share"].endswith(p["url"]), True)
    check("no four-letter type", "type" in p or "wrapd" in plant, False)


# Quick fixes: of the times a check got in the way, how often the agent fixed it
# instead of going around it. Read from tool calls, so the language doesn't matter.
def claude_session(root, sid, steps):
    rows = []
    for i, (kind, content) in enumerate(steps):
        r = {
            "cwd": "/nowhere/mill",
            "sessionId": sid,
            "type": kind,
            "timestamp": f"2026-07-01T10:{i // 60:02d}:{i % 60:02d}Z",
            "message": {"role": kind, "content": content},
        }
        if kind == "assistant":
            r["message"].update(id=f"{sid}{i}", usage={"input_tokens": 5})
        rows.append(r)
    jl(root / "-nowhere-mill" / f"{sid}.jsonl", rows)


def run(cid, cmd, failed=False):
    return [
        (
            "assistant",
            [
                {
                    "type": "tool_use",
                    "id": cid,
                    "name": "Bash",
                    "input": {"command": cmd},
                }
            ],
        ),
        (
            "user",
            [
                {
                    "type": "tool_result",
                    "tool_use_id": cid,
                    "content": "Exit code 1" if failed else "ok",
                    "is_error": failed,
                }
            ],
        ),
    ]


def edit(path, old, new):
    inp = {"file_path": "/nowhere/mill/" + path, "old_string": old, "new_string": new}
    return [
        (
            "assistant",
            [{"type": "tool_use", "id": path + new, "name": "Edit", "input": inp}],
        )
    ]


def quick_factor(claude, codex):
    data = wrapped.collect(claude, codex, 2026)
    return next(f for f in data["factors"] if f["key"] == "quick")


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    claude_session(
        tmp / "claude",
        "q1",
        [("user", "the login test started failing")]
        # fixed for real
        + run("c1", "pytest", failed=True)
        + edit("src/login.py", "a", "b")
        + run("c2", "pytest")
        # a retry fails, then the agent silences the check: gone around
        + run("c3", "ruff check .", failed=True)
        + edit("src/a.py", "y", "z")
        + run("c4", "ruff check .", failed=True)
        + edit("src/a.py", "x = 1", "x = 1  # noqa")
        + run("c5", "ruff check .")
        # keeping a suppression that was already there is still a real fix
        + run("c6", "pnpm lint", failed=True)
        + edit(
            "src/b.ts", "a = b // eslint-disable-line", "a = c // eslint-disable-line"
        )
        + run("c7", "pnpm lint")
        # a passing check with nothing broken is not a fix; skipping the hooks goes around one
        + run("c8", "pnpm lint")
        + run("c9", "git commit --no-verify -m wip")
        + [("user", "gracias")],
    )
    patch = "*** Begin Patch\n*** Update File: web/cart.test.ts\n-  it('totals'\n+  it.skip('totals'\n*** End Patch"
    jl(
        tmp / "codex" / "sessions" / "2026" / "07" / "02" / "rollout-fix.jsonl",
        [
            {"timestamp": f"2026-07-02T09:00:0{i}Z", "type": kind, "payload": payload}
            for i, (kind, payload) in enumerate(
                [
                    ("session_meta", {"id": "k", "cwd": "/nowhere/shop"}),
                    (
                        "response_item",
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "por qué falla el build?",
                                }
                            ],
                        },
                    ),
                    (
                        "response_item",
                        {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": "k1",
                            "arguments": '{"cmd":"pnpm test"}',
                        },
                    ),
                    (
                        "response_item",
                        {
                            "type": "function_call_output",
                            "call_id": "k1",
                            "output": "Process exited with code 1",
                        },
                    ),
                    (
                        "response_item",
                        {
                            "type": "custom_tool_call",
                            "name": "apply_patch",
                            "call_id": "k2",
                            "input": patch,
                        },
                    ),
                    (
                        "response_item",
                        {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": "k3",
                            "arguments": '{"cmd":"pnpm test"}',
                        },
                    ),
                    (
                        "response_item",
                        {
                            "type": "function_call_output",
                            "call_id": "k3",
                            "output": "Process exited with code 0",
                        },
                    ),
                ]
            )
        ],
    )
    q = quick_factor(tmp / "claude", tmp / "codex")
    # 2 real fixes; a silenced check, a skipped test and --no-verify went around
    check("share of checks fixed for real", q["hero"], "40%")
    check("the meter shows the headline", q["score"], 40)

# No fixes seen (Cursor keeps no edits): what you asked for decides it.
plain = [("user", f"add the export button to page {i}") for i in range(150)]
with tempfile.TemporaryDirectory() as tmp:
    claude_session(Path(tmp), "p1", plain)
    check(
        "never asking for a shortcut leans root cause",
        quick_factor(Path(tmp), Path(tmp) / "x")["score"] >= 70,
        True,
    )
with tempfile.TemporaryDirectory() as tmp:
    asks = [
        ("user", t)
        for t in ["just make the tests pass", "quick fix please", "workaround is fine"]
        * 10
    ]
    claude_session(Path(tmp), "h1", asks + plain[:70])
    check(
        "asking for shortcuts leans band-aid",
        quick_factor(Path(tmp), Path(tmp) / "x")["score"] < 30,
        True,
    )


def scored(**kw):
    return [
        {"key": k, "score": kw.get(k, 50)}
        for k in ("babysit", "quick", "verify", "trust", "line")
    ]


recap = {
    "agent_hours": 310,
    "longest_run_minutes": 103,
    "stepped_in": 307,
    "context_words": 89184,
}
hands_off = wrapped.profile(scored(babysit=92, verify=30), recap)
check("strongest lean names the archetype", hands_off["name"], "Night Shift Manager")
check(
    "link encodes archetype and the card's numbers",
    hands_off["url"],
    "https://dosu.dev/wrapd?a=night-shift-manager&n=310.103.307.89184",
)
check("weakest factor gets the tip", hands_off["try"], wrapped.TIPS["verify"])
check(
    "article",
    wrapped.profile(scored(quick=5), recap)["share"].startswith(
        "I'm a Duct Tape Specialist"
    ),
    True,
)
check(
    "an before a vowel",
    wrapped.profile(scored(verify=3), recap)["share"].startswith(
        "I'm an Express Shipper"
    ),
    True,
)
check("no book under one copy", wrapped.pick_books(5_000), [])
shelf = wrapped.pick_books(6_600_000_000)
check(
    "shelf spans biggest, War and Peace, smallest",
    [b["title"] for b in shelf],
    ["English Wikipedia", "War and Peace", "The Great Gatsby"],
)
check("War and Peace copies", shelf[1]["copies"], round(6_600_000_000 / 780_000))
check(
    "small year still gets War and Peace",
    [b["title"] for b in wrapped.pick_books(1_000_000)],
    ["War and Peace", "Moby-Dick", "The Great Gatsby"],
)
check("books in data", "books" in data, True)

# Privacy: the collector can only import these, and the page can't reach the network.
ALLOWED_IMPORTS = {
    "__future__",
    "argparse",
    "base64",
    "collections",
    "datetime",
    "json",
    "os",
    "pathlib",
    "re",
    "statistics",
    "sys",
    "webbrowser",
}
tree = ast.parse(Path(wrapped.__file__).read_text())
imported = {
    a.name.split(".")[0]
    for n in ast.walk(tree)
    if isinstance(n, ast.Import)
    for a in n.names
}
imported |= {
    n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
}
check("collector imports only the allowlist", sorted(imported - ALLOWED_IMPORTS), [])
page = (Path(__file__).parents[1] / "assets" / "template.html").read_text()
check(
    "page blocks all network but dosu.dev fonts via CSP",
    "default-src 'none'" in page
    and "connect-src" not in page
    and "font-src https://dosu.dev;" in page,
    True,
)
# Besides the fonts, the only URLs are links, opened only when clicked.
check(
    "page references no external URL but dosu.dev",
    sorted(set(re.findall(r"https?://(?!www\.w3\.org/2000/svg)[^\s\"'<>;]+", page))),
    [
        "https://dosu.dev",
        "https://dosu.dev/?utm_source=wrapd&utm_medium=wrap&utm_content=card",
        "https://dosu.dev/?utm_source=wrapd&utm_medium=wrap&utm_content=pitch",
        "https://dosu.dev/fonts/PPMondwest-Regular.woff2",
        "https://dosu.dev/fonts/mona-sans-latin.woff2",
        "https://www.linkedin.com/sharing/share-offsite/?url=",
        "https://x.com/intent/post?text=",
    ],
)

check("abbr T", wrapped.abbr(1_700_000_000_000), "1.7T")
check("abbr B", wrapped.abbr(8_400_000_000), "8.4B")
check("abbr M", wrapped.abbr(123_456_789), "123M")
check("abbr K", wrapped.abbr(12_345), "12.3K")
check("abbr small", wrapped.abbr(999), "999")

check("t3 worktree", wrapped.project_name("/Users/x/.t3/worktrees/dosu/t3-abc"), "dosu")
check(
    "conductor",
    wrapped.project_name("/Users/x/conductor/workspaces/dosu/des-moines"),
    "dosu",
)
check(
    "claude worktree",
    wrapped.project_name("/Users/x/code/repo/.claude/worktrees/foo"),
    "repo",
)
check(
    "repo url", wrapped.project_name("/x/y", "git@github.com:dosu-ai/dosu.git"), "dosu"
)
check("plain dir", wrapped.project_name("/nowhere/acme"), "acme")

if failures:
    print("\n".join(failures))
    sys.exit(1)
print("ok")
