# guided-review

A Claude Code skill that turns a branch or pull request diff into a guided
walkthrough page. You open it next to the PR during the final human review, read one
chapter at a time, and mark files as viewed on your code host as you go.

It's a guide, not a reviewer. It doesn't hunt for bugs or comment on style. It
explains what changed and in what order to read it.

## What the page shows

- **Overview:** one sentence of context, the main changes, and before/after diagrams
  of the change.
- **Chapters:** files grouped by idea, such as a screen change, a logic change or an
  API change, with the core change first. Each chapter has a short summary, an
  optional diagram, at most one "Look at" pointer, and its files in reading order
  with +/− counts. Files link into the PR's diff on GitHub, GitLab and Bitbucket.
- **Progress:** a "Reviewed" tick per chapter, saved in your browser.
- **Skipped:** lock files and generated files are listed by name and never read.

Diagrams are Mermaid flowcharts drawn in the page's light or dark theme. New parts
are green, changed parts amber, and removed parts red.

## Install

You need Claude Code, `git` and `python3` (standard library only). Tested on Claude
Code 2.1.292.

The skill runs in its own lightweight agent (`agents/guided-review.md`). That keeps
the diff out of your main conversation and runs on Sonnet. Install both the skill
and the agent.

### With skillshare

```sh
skillshare install github.com/AnimeshRy/skills --skill guided-review
skillshare sync
ln -s ~/.config/skillshare/skills/guided-review/agents/guided-review.md ~/.claude/agents/guided-review.md
```

### By hand

```sh
git clone --depth 1 https://github.com/AnimeshRy/skills /tmp/animeshry-skills
cp -r /tmp/animeshry-skills/guided-review ~/.claude/skills/
mkdir -p ~/.claude/agents
cp ~/.claude/skills/guided-review/agents/guided-review.md ~/.claude/agents/
```

Restart Claude Code, or start a new session, so it picks up the skill and the agent.

## Use

From inside the repository you're reviewing:

```text
/guided-review                                  # current branch vs the default branch
/guided-review main feature/coupons             # any base and head
/guided-review main HEAD <pr-url>               # also link each file into the PR diff
/guided-review abc123^1 abc123 <pr-url>         # an already-merged PR, by merge commit
```

It runs in the background and replies with a link to the page. With Claude Code's
Artifact tool, that's a private claude.ai page you can share. Without it, the skill
opens a local HTML file in your browser.

## How it works

1. `guide.py collect` lists the changed files and sorts them into core, tests and
   skipped. It prints a slimmed diff of the core files: one line of context, without
   markup-only lines. New files over 150 lines are shown as an outline instead of in
   full.
2. The agent groups the files into chapters and writes a small `data.json`.
3. `guide.py build` checks the JSON and renders `template.html`. It checks that every
   core file sits in one chapter, the word limits, and the diagram rules: at most 10
   nodes, one connected graph, and no hand-set colours. Then it renders the page.

The model never writes HTML or CSS, so output stays small and every guide looks the
same.

## Cost

Reading the diff is the main cost. On a 3,500-line frontend PR (33 files), one run
used about 117k tokens over 7 tool calls in under 3 minutes on Sonnet. Small PRs cost
much less.

## Limits

- Each file belongs to one chapter. A file that serves several chapters is linked
  from the others with `also`.
- `build` can't check that diagram labels are real identifiers from the code. The
  skill tells the model to use only names from the diff.
- File links need a PR URL. If none is given, the skill tries `gh pr view` when the
  GitHub CLI is installed.
