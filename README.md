# Agent Wrap'D

A skill for Claude Code, Codex and Cursor that turns a year of your agent sessions into a short story you can share. It reads the logs on your machine and scores how you run your agents on five factors:

- babysitting
- quick fixes
- verification
- where your trust comes from
- how many agents you run at once

Then it names your archetype and draws a card you can post.

## Install

```bash
npx skills add dosu-ai/wrapD -g -y
```

Then use `/agent-wrapd` in your agent. It writes `~/Downloads/agent-wrapd-<year>.html` and opens it.

## Privacy

Everything runs locally in one standard-library Python file, `skills/agent-wrapd/scripts/wrapped.py`, which never opens a network connection. The generated page blocks every request except Dosu's two web fonts from dosu.dev, which carry nothing about you. Nothing leaves your computer unless you share your card.

## Development

```bash
python3 skills/agent-wrapd/scripts/test_wrapped.py
```

Shared cards link to `https://dosu.dev/wrapd`, which redraws the card from the `?a=&n=` params. Keep the archetypes and card order in `wrapped.py` in sync with the site.
