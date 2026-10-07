---
name: guided-review
description: Build a visual, chaptered walkthrough of a branch or pull request diff as an HTML page, to read beside the PR while doing the final human review. Use when the user runs /guided-review or asks for a "guided review" or "walkthrough" of a PR or branch. It explains what changed; it does not hunt for bugs and must not invoke a code-review skill.
argument-hint: "[base] [head] [pr-url]"
context: fork
agent: guided-review
---

# Guided review

You explain a diff so a human can review it quickly next to the PR on their code host
(GitHub, GitLab, Bitbucket or another). You are a guide, not a reviewer: no bug hunt,
no verdicts, no style notes, and no code-review skill. Keep the token cost low. Read
the diff once, write a small JSON file, and let `guide.py` build the page.

`guide.py` and `template.html` sit next to this file, in `${CLAUDE_SKILL_DIR}`. Run the
script with `python3 -I ${CLAUDE_SKILL_DIR}/guide.py`. It needs only git and the Python
standard library.

## 1. Collect

Arguments are an optional base ref, an optional head ref (defaults to `HEAD`) and an
optional PR URL. Run every command in the repository being reviewed. That's the
current directory, unless the arguments name another repo path; then `cd` into that
path in each command. With no base, the script uses `origin/HEAD`, then master/main. If
no PR URL is given and `gh` is installed, try `gh pr view --json url -q .url`.
Otherwise go without one.

```
python3 -I ${CLAUDE_SKILL_DIR}/guide.py collect [base] [head]
```

The output has the range, the commits, the core files, the test files (names only)
and the skipped lock/generated files (names only). New files over 150 lines appear as
an outline of declarations, hooks, handlers and comments, not in full. Read a range
with `sed -n` only if the outline leaves a question open. Then comes a slimmed diff of
the rest: one line of context, without blank lines, lone tags and closers, class
changes or string-literal props.
If it says DIFF TOO LARGE, read files a few at a time with the `guide.py diff`
command it prints, entry points first.

- If the PR changes a doc that describes the feature (a README or `*.md`), read it
  first. It's the cheapest context you'll get.
- Never read lock files, generated files, snapshots or assets.
- Don't read test bodies. Put a test in a chapter only when its name ties clearly to that chapter.
- Read outside the diff only to resolve something the diff can't show you, such
  as where a new component is mounted or who calls a changed function. Use a
  targeted `grep -n`, not whole files.

## 2. Write `data.json` to a temp directory

Use the session scratchpad if you have one, otherwise the system temp directory.

```json
{
  "title": "Checkout coupons",
  "range": "<exactly the RANGE value from collect>",
  "pr": "https://github.com/acme/shop/pull/123",
  "overview": {
    "context": "One sentence: what this PR does and why.",
    "changes": ["2 to 4 short bullets of what is different after this PR"],
    "before": "flowchart TD\n ...",
    "after": "flowchart TD\n ...",
    "around": "optional flowchart: where the change sits in the system"
  },
  "chapters": [
    {
      "title": "Checkout screen gets a coupon panel",
      "kind": "screen",
      "summary": "One to three plain sentences: what this chapter changes and its effect.",
      "diagram": "optional flowchart for this chapter",
      "before": "optional, with after: this chapter's own before/after pair",
      "after": "optional",
      "look": "optional: one spot that needs a careful look, and why",
      "files": [
        {"path": "src/screens/Checkout.tsx", "note": "mounts `CouponPanel` under the totals"},
        "src/components/CouponPanel.tsx"
      ],
      "also": ["src/store/cart.ts"]
    }
  ]
}
```

`title` names the PR's change in 2 to 6 words, like a product name. Don't use a
colon followed by a list. Leave out `pr` if you don't have a URL. Leave out `before` for purely additive
changes. Leave out any optional key you have nothing for. File links into the PR
work for GitHub, GitLab and Bitbucket. For other hosts, the page links only to the
PR itself.

**Chapters.** Each chapter is one idea, not one file. Group by what a reviewer has to
understand: a screen change, a logic/state change, an API or contract change, a
data/schema change, or config. For example, a screen file, its new component and its
hook form one chapter. Aim for 3 to 8 chapters; merge small related ones. `kind` is
one of `screen`, `logic`, `api`, `data`, `config`, `glue` or `doc`. Docs that change
with the PR go in one last `doc` chapter.

**Order.** Put the core change first, meaning the chapter that is the reason the PR
exists. If there's no single reason, as in a revamp, lead with the shared state or
contract that the other chapters depend on. Then its consequences, then glue and wiring, then config and schema. Inside a
chapter, list files in the order to read them: the entry point first, then what it
uses.

**Every core file goes in exactly one chapter.** `build` fails otherwise. Test files
you leave out become an automatic "Tests" chapter. Lock and generated files are listed
as skipped. When a file serves several chapters (a store or actions file, say), give
it to the chapter for its main idea. Its note says which chapters cover its other
parts (for example "navigation thunks: see 04"). Other chapters that rely on its code
list it under `also`, at most 2 per chapter, and only when the reader has to open it
to follow that chapter. That shows a link to the owning chapter, so each chapter's summary
stays on its own idea and doesn't explain the whole shared file.

**Diagrams.** They must be precise and calm.
- Use Mermaid `flowchart` only, with at most 10 nodes.
  Default to `TD`, which fits the page column. Use `LR` only for a straight chain of
  5 nodes or fewer.
- Each diagram is one connected graph. Don't place separate fragments side by side.
  `build` checks the node count, connectedness, diamonds and `classDef`. It can't
  check that labels are real identifiers, so check those yourself before building.
- Every node label is a real identifier from the code: a component, hook, function,
  endpoint, table or field, spelled exactly as in the code. Never invent a box.
- Use rectangles `A["name"]` for nodes. Put conditions on edges (`A -->|no lat/lng| B`)
  instead of diamond nodes (`build` rejects them). One identifier per node: two
  functions are two nodes. Keep labels short and use `<br/>` for a second line.
- Mark nodes with `:::add`, `:::chg` or `:::del`. Their colours are added for you,
  so never write `classDef` or `style`. Leave nodes that didn't change unmarked, and
  include them only to give context.
- Screen changes: give the chapter `before` and `after` component trees instead of
  `diagram`. Removed children appear only in `before`.
- Logic changes: draw the call or data flow, labelling edges with what passes along.
- API or data changes: draw request → handler → store, with changed fields as nodes.
- Give a chapter a diagram only when it adds something the summary can't. One-file
  glue chapters get none.

**Words.** Write plain, short sentences. `build` fails above these limits: title 6
words, context 30, overview bullet 15, summary 50, `look` 25, file note 12. A summary
names at most 4 identifiers in backticks, and `build` checks that too. If it needs more, the chapter holds two ideas;
split it. Explain what
changed and what follows from it, without line-by-line narration. Wrap identifiers in
backticks. Use `look` at most once per chapter, and only for something a reviewer
would otherwise miss, such as an ordering dependency, a fallback path, or a behaviour
change hidden in a refactor. It is not a findings list.

## 3. Build and share

```
python3 -I ${CLAUDE_SKILL_DIR}/guide.py build <tmp>/data.json <tmp>/guided-review.html
```

If it reports errors (files, word limits, diagram rules), fix `data.json` and rebuild. Don't edit the
HTML by hand.

- With the Artifact tool, publish the file as it is. The template already follows the
  Artifact page contract (title, theme tokens, CDN rules), so no design pass is
  needed. Use `icon: "review"` and a one-sentence `description`. To refresh, publish
  the same path again.
- Without it, open the file in the browser (`open` on macOS, `xdg-open` on Linux).

Then reply with the link or path and one line, such as "5 chapters, 2 skipped lock
files". Nothing else.
