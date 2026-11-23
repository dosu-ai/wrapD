---
name: agent-wrapd
description: Builds Agent Wrap'D, a short click-through story that reads your local Claude Code, Codex and Cursor logs and scores how you run your agents on five factors (babysitting, quick fixes, verification, where your trust comes from on the trust pyramid, how many agents you run at once), then names your archetype with one thing to try and a shareable line. Use when the user says "agent wrap'd", "wrapd", "agentic wrapped", "my agent wrapped", "wrap my year", "/agent-wrapd", or asks for a year-in-review or agent personality test from their agent sessions.
argument-hint: "[year]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/wrapped.py *)
---

# Agent Wrap'D

Everything is precomputed by a script and rendered by a fixed template, so this skill costs only a few tokens. Don't read the logs, the template, or the generated HTML yourself.

1. See what's on this machine (counts only, no conversation content):

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/wrapped.py --scan
   ```

   It lists every log folder it found (one per Claude Code config dir, e.g. `claude` and `claude-personal`, plus `codex` and `cursor`) and the top projects with session and token counts. Add `--year <YYYY>` if the user named a year.

2. Unless the user already said what to include, ask once, in a single question (use your multiple-choice question tool if you have one), with "everything" as the default:
   - which sources (accounts/agents) to include;
   - all projects, only some, or all except some (offer the project names from the scan);
   - whether they plan to share it: if so, suggest `--private`, which hides session titles, file names and branches.

3. Run it with their choices:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/wrapped.py [--sources claude,codex] [--projects a,b | --exclude-projects c] [--private]
   ```

   It writes `~/Downloads/agent-wrapd-<year>.html` and opens it in the browser. Nothing is uploaded.

4. Reply with the two lines it printed (the file path and the summary line) and nothing else. Mention once that arrow keys, Space or a click advance the story; nothing advances on its own.

Other options, only if the user asks: `--year`, `--out PATH`, `--name NAME`, `--no-open`, `--json` (also writes the raw stats next to the HTML).

## Notes

- Privacy: one readable stdlib Python file (runs on Python 3.9+, including the one macOS ships) and one HTML template, no dependencies or binaries. The script never touches the network; the test pins its imports to a standard-library allowlist. The generated page carries a Content-Security-Policy with `default-src 'none'`, so the browser itself refuses any request but Dosu's two web fonts from dosu.dev (offline, it falls back to Georgia and the system sans). The docstring at the top of `scripts/wrapped.py` lists exactly what is read and written.
- Claude Code deletes transcripts after `cleanupPeriodDays` (30 by default), so its share only covers what is still on disk. `--scan` and `--json` show each source's first date.
- Tokens are everything the model processed: input, cache writes, cache reads and output. Cursor transcripts carry no token counts, so Cursor adds sessions and turns only.
- The five factors, each a 0-100 spectrum with one headline stat:
  - Babysitting: the median stretch of agent work between two of your messages (vs a guessed typical 1.5 min), against interrupts, status checks and redirects. Headline: the longest an agent worked without hearing from you (active time, 5-minute idle cap).
  - Quick fixes: read from tool calls, so it works in any language. A check got in the way when it failed and the agent edited and ran it again, or when a command went around it (`--no-verify`, `--deselect`, `SKIP=`). It was fixed for real unless the edits added a skipped test or a silenced error (`@ts-ignore`, `# noqa`, `.skip(`, `except: pass`…). Score and headline are the same number: the share fixed for real. With no fixes seen (Cursor keeps no edits), it falls back to asks for a quick fix against asks for the root cause (English, Spanish, Portuguese, French, German), weighed against a guessed typical rate, so never asking leans root cause. Fixes that changed a test and digging through git history (`blame`, `bisect`, `log -S/-p`) land in the evidence.
  - Verification: share of hand-driven sessions that changed code and also ran tests, lint, type checks or a runtime check.
  - Trust: the trust pyramid (codebase, static analysis, verification, prose, human review). Code edits are sorted by path into code, tests/lint/type config, and rule files (AGENTS.md, CLAUDE.md, SKILL.md, Cursor rules); runtime checks are browser/Playwright/preview tools and curl against localhost; human review is your corrections, interrupts and rejected tool calls. The score is enforced edits against prose plus human review.
  - The line: average and peak sessions active in the same ten minutes, plus subagents per session (vs a guessed typical 1.3 sessions).
- The archetype is the factor that leans hardest, at its end; the tip comes from the lowest factor. It's a nudge, not a test. No four-letter type on purpose.
- After the factors, one slide pitches Dosu: it quotes how many times you stepped in (each one a moment an agent got stuck) and links to early access on `https://dosu.dev`.
- The final card shows the archetype and four numbers: hours your agents worked, the longest an agent went without you, times you stepped in, and words of context you wrote. The agents' side (least reliable tool, rage prompts, rejections) and the older year stats (biggest day, models, tools, genres) are still computed and land in `--json`.
- Sharing: the Share button opens a dialog with the final card drawn as a 1080×1350 PNG on a canvas: copy or download the image (or share it from the phone's share sheet), copy the link, or post to LinkedIn or X. The line ends in `https://dosu.dev/wrapd?a=<archetype>&n=<four numbers>`, which the marketing site (`dosu-ai/dosu`: `frontend/marketing/src/app/(landing)/wrapd`) turns into a page and a preview image of the result. Keep `ARCHETYPES` and the `CARD` order in `wrapped.py` in sync with `frontend/marketing/src/lib/wrapd.ts` in `dosu-ai/dosu`; the site accepts only known archetypes and plain integers. The page links to `https://dosu.dev` only from the pitch slide and the "What's Dosu?" button; both links carry `utm_source=wrapd&utm_medium=wrap` (and `utm_content=pitch` or `card`), so the site can count clicks from a wrap. Nothing is sent unless a link is clicked.
- The factors use human-typed turns from hand-driven sessions only. Headless runs (`codex exec`, `claude -p`, openers repeated in 3+ sessions) and subagents are counted separately as delegation. The "typical" values are medians from a sample of hand-driven sessions; the ones marked as guesses in `wrapped.py` are not. Task genre, redirects and go-aheads are keyword heuristics in English and Spanish.
- Look: Dosu's brand, as on dosu.dev: PP Mondwest for headings and numbers, Mona Sans for copy, mono caps for labels and buttons, the landing palette (dark, night, parchment, cream, gold, pink), the gold rule with diamond ends, and segmented meters like a game's power bar. The opening slide is a title screen with the logomark; the hours slide shows the factory skyline and the pitch slide the conveyor (both from `frontend/marketing/public/adx` in `dosu-ai/dosu`, scaled down). D (`assets/dosu-d.png`) sits on the final card, which is sized to screenshot. Every image is inlined as a data URI. Each slide opens as a circle from where you tapped. The trust factor's cost ordering comes from poteto's talk "how do I trust my agents more?".
- Editing the story: copy and slide order live in `assets/template.html`; stats live in `scripts/wrapped.py`. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/test_wrapped.py` after changing either.
