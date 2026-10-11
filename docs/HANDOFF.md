# Handoff — "whats-new": daily AI news routine

**Date:** 2026-10-11
**Workspace:** `/home/foliveirafilho/Documents/projects/whats-new`. Empty directory, not yet a git repo.
**Status:** Design is finished: every decision below came out of a `/grill-me` session. No code, specs or other artifacts exist yet.

## Goal
A local routine that runs every day and finds what's new in AI, so the user keeps learning as things come out. It writes a report with the findings, their links, and ideas for products that could be built with them.

## Decisions (all confirmed by the user unless marked)

| Area | Decision |
|---|---|
| Runtime | Local `claude -p` (headless), not a cloud routine |
| Scheduling | **systemd user timer**, 07:30 local, `Persistent=true` so a missed run happens at boot |
| Coverage window | Everything **since the last successful run**, not a fixed 24h. The last-success timestamp only advances when a report is actually written |
| Sources | (1) AI lab and vendor blogs via RSS: Anthropic, OpenAI, Google DeepMind, Meta AI, Mistral, Hugging Face, Vercel, AWS ML. (2) Tech news via RSS: The Verge AI, TechCrunch AI, Ars Technica, VentureBeat AI, The Decoder. (3) Community signals: Hacker News (AI-filtered), GitHub Trending, HF trending models/papers, Product Hunt AI. **Excluded:** arXiv and newsletters |
| Pipeline | **The script fetches and Claude curates.** A Python fetch script pulls the sources, drops URLs already in SQLite, and writes `candidates.json`. Then `claude -p` groups duplicates, ranks, reads the full pages of the top items only, and writes the report |
| Model | Sonnet |
| Volume | **5–10 featured stories plus a one-line skim list** of the other notable items |
| Per featured story | A summary and all source links; "Why it matters" (explains the concept); "Try it in 15 min" (a concrete hands-on step); product ideas, at most one per viewpoint and only when the idea is real, each with an effort estimate (hours, days or weeks) |
| Idea viewpoints | (a) The user's work context: real estate, short-term rental operations, sales/CRM (Pipedrive), internal automations and agents. (b) Indie side projects with a monetization angle. (c) Dev tooling for the user's stack: Claude Code, Next.js/Vercel, Supabase, agents/MCP. **No** generic startup ideas |
| Personalization | A `profile.md` the user edits by hand (role, stack, work areas, interests). Ranking uses it. **No automatic feedback loop** |
| Language | English |
| Delivery | `reports/YYYY-MM-DD.md` in the repo, plus a desktop notification (`notify-send`) with the headline and file path |
| Failures | A dead source is skipped and listed in a "Sources health" footer. If `claude -p` fails, retry once after 10 minutes, then send an error notification and keep the candidates for the next run |
| Security (decided by the assistant, user did not object) | The Claude step reads untrusted web content, so it gets only `WebFetch` and write access under `reports/`. No Bash, no MCP connectors. Use `--allowedTools` / permission settings to enforce this |

## Open items / to do next
- The user hasn't yet chosen between **building now** and **producing a PRD plus task DAG first** (via `/deep-handoff` or the esteira). Ask if it's still unclear.
- Create the git repo and initial structure: fetch script, prompt file, `profile.md` with placeholders, `systemd/` units, `state/` (SQLite, last-run timestamp, pending candidates).
- Check the RSS URL for each source. Some labs have no official feed (Anthropic, possibly Meta AI) and need scraping or a fallback.
- Decide on Python tooling. `uv` is suggested. This wasn't discussed.
- Do one end-to-end test run and check that the notification works on this Linux desktop.
- The user has to fill in `profile.md` before ranking is meaningful.

## User's global rules to respect (from ~/.claude/CLAUDE.md)
- Delegate work to subagents by default. Pass the rules below on to them.
- New development goes in a **git worktree**. This is currently not a git repo, so after `git init` ask which base branch to use if that matters.
- Every commit ends with `Co-Authored-By: Claude <model> <noreply@anthropic.com>`. Commit only when the user asks.
- Use `rtk` for shell commands. A hook rewrites them automatically.
- The user's internal product-workflow plugin ("esteira") expects product work to go through its skills.

## Suggested skills
- `esteira:refinamento-rapido` or `esteira:fatiamento`: the scope is already clear (porta C/B), so either slice it into tasks or close any remaining unknowns.
- `deep-handoff` → `deep-execute`: if the user wants a PRD and a task DAG executed by agents.
- `claude-code-guide` agent: for the exact `claude -p` flags (`--allowedTools`, `--model`, output format) and headless permission settings.
- `mattpocock-skills:tdd`: for the fetch, dedup and state logic.
- `run`: for the end-to-end test run.
