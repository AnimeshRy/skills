#!/usr/bin/env python3
"""Guided review helper.

  guide.py collect [base] [head]     filtered diff for the model to read
  guide.py diff <range> <path>...    the same slimmed diff for a few files
  guide.py build data.json out.html  validate chapters and render the page
"""

import fnmatch
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "template.html"
DIFF_LINE_BUDGET = 3000
BIG_NEW_FILE = 150  # new files longer than this are shown as an outline

# Diff lines that cost tokens but explain nothing: blank, a class change, a lone
# tag or closer, or a string-literal prop. Props bound to code ({...}) are kept.
NOISE_LINE = re.compile(
    r"^[+-]\s*((className|class)=.*|</?[A-Za-z.]*\s*/?>|/?>|[)}\]]+[;,]?"
    r"|[\w-]+=(\"[^\"]*\"|'[^']*')|\{?/\*.*\*/\}?)?\s*$"
)
# Lines that give a new file's shape: declarations, hooks, handlers, comments.
OUTLINE_LINE = re.compile(
    r"^\s*(export\b|(async\s+)?function\b|(async\s+)?def\b|class\b|interface\b|type\s+\w+\s*="
    r"|//|#\s|/\*\*|const\s+\w+\s*=\s*(async\s*)?\(|const\s+\[\w+,\s*set|const\s+[A-Z][A-Z0-9_]+\s*="
    r"|.*\buse[A-Z]\w*\(|.*\bdispatch\(|.*\b(fetch|axios)\b)"
)
# Max words per field; build fails above these.
# Kept in step with the Words section of SKILL.md.
WORD_LIMITS = {
    "title": 6, "context": 30, "change": 15, "summary": 50, "look": 25, "note": 12,
}
MAX_SUMMARY_IDENTIFIERS = 4
MAX_DIAGRAM_NODES = 10
ARROW = re.compile(r"\s*(?:<?[-=.]{2,}[->ox]|<?--[-.]*>?|~~~)\s*")

# Patterns containing "/" match the whole path, others match the file name.
NOISE = [
    "*.lock", "package-lock.json", "pnpm-lock.yaml", "go.sum", "npm-shrinkwrap.json",
    "*.min.js", "*.min.css", "*.map", "*.snap", "*.svg", "*.png", "*.jpg", "*.jpeg",
    "*.gif", "*.webp", "*.ico", "*.woff", "*.woff2", "*.ttf", "*.otf", "*.pdf",
    "*.generated.*", "*_pb2.py", "*_pb2_grpc.py", "*.pb.go", ".coverage",
    "*/dist/*", "*/build/*", "*/__snapshots__/*", "*/generated/*",
    "*/locales/*.json", "*/i18n/*.json", "*/vendor/*", "*/node_modules/*",
]
TESTS = [
    "test_*.py", "*_test.py", "*_test.go", "*.test.*", "*.spec.*", "conftest.py",
    "*/tests/*", "*/test/*", "*/__tests__/*", "*/e2e/*", "*/cypress/*",
]
STATUS = {
    "A": ("A", "added"),
    "M": ("M", "modified"),
    "D": ("D", "deleted"),
    "R": ("R", "renamed"),
    "C": ("C", "copied"),
    "T": ("M", "type changed"),
}


def git(*args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def matches(path, patterns):
    name = path.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatch("/" + path, p) if "/" in p else fnmatch.fnmatch(name, p)
        for p in patterns
    )


def classify(path):
    if matches(path, NOISE):
        return "noise"
    return "test" if matches(path, TESTS) else "core"


def changed_files(rng):
    files = {}
    parts = git("diff", "--numstat", "-z", "-M", rng).split("\0")
    i = 0
    while i < len(parts) and parts[i]:
        add, dele, path = parts[i].split("\t")
        old = None
        if path:
            i += 1
        else:  # rename: path is empty, old and new follow as separate fields
            old, path = parts[i + 1], parts[i + 2]
            i += 3
        files[path] = {"path": path, "add": add, "del": dele, "old": old, "status": "M"}
    parts = git("diff", "--name-status", "-z", "-M", rng).split("\0")
    i = 0
    while i < len(parts) and parts[i]:
        status = parts[i][0]
        step = 3 if status in "RC" else 2
        path = parts[i + step - 1]
        if path in files:
            files[path]["status"] = status
        i += step
    return list(files.values())


def default_base():
    for ref in ("origin/HEAD", "origin/master", "origin/main", "master", "main"):
        found = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", ref + "^{commit}"],
            capture_output=True,
        )
        if found.returncode == 0:
            return ref
    sys.exit("No base branch found. Pass one: guide.py collect <base> [head]")


def counts(f):
    return "bin" if f["add"] == "-" else f"+{f['add']} -{f['del']}"


def collect(base=None, head="HEAD"):
    base = base or default_base()
    base_sha = git("rev-parse", "--short", base).strip()
    head_sha = git("rev-parse", "--short", head).strip()
    rng = f"{base_sha}...{head_sha}"
    files = changed_files(rng)
    groups = {"core": [], "test": [], "noise": []}
    for f in files:
        groups[classify(f["path"])].append(f)

    print(f"RANGE {rng}   ({base} ... {head})")
    print("\nCOMMITS")
    print(git("log", "--format=  %h %s", "-n", "30", f"{base_sha}..{head_sha}"))
    print(f"CORE FILES ({len(groups['core'])}) - every one must go in a chapter")
    for f in groups["core"]:
        moved = f" (from {f['old']})" if f["old"] else ""
        print(f"  {f['status']} {counts(f):>11}  {f['path']}{moved}")
    print(f"\nTEST FILES ({len(groups['test'])}) - not read; optional in chapters")
    for f in groups["test"]:
        print(f"  {f['status']} {counts(f):>11}  {f['path']}")
    noise = [f["path"] for f in groups["noise"]]
    print(f"\nSKIPPED ({len(noise)}): {', '.join(noise) or '-'}")

    big = [
        f for f in groups["core"]
        if f["status"] == "A" and f["add"] != "-" and int(f["add"]) > BIG_NEW_FILE
    ]
    for f in big:
        rows = [
            f"  {n:>4}: {line.strip()[:120]}"
            for n, line in enumerate(git("show", f"{head_sha}:{f['path']}").splitlines(), 1)
            if OUTLINE_LINE.match(line)
        ]
        print(f"\nNEW FILE OUTLINE {f['path']} ({f['add']} lines; read ranges with sed -n if needed)")
        print("\n".join(rows[:80]))

    paths = [p for f in groups["core"] if f not in big for p in (f["path"], f["old"]) if p]
    if not paths:
        return
    diff = slim_diff(rng, paths)
    lines = diff.count("\n")
    if lines > DIFF_LINE_BUDGET:
        print(
            f"\nDIFF TOO LARGE ({lines} lines). Read core files a few at a time,"
            f" most important first:\n  python3 -I {Path(__file__).resolve()} diff {rng} <paths>"
        )
    else:
        print(f"\nDIFF ({lines} lines)\n{diff}")


def slim_diff(rng, paths):
    diff = git("diff", "-U1", "-M", rng, "--", *paths)
    return "".join(line for line in diff.splitlines(True) if not NOISE_LINE.match(line))


def md(text):
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text or ""))


def mermaid(src):
    # Rendered in the page so the colours follow its light/dark theme.
    return f'<div class="diagram" data-src="{html.escape(src.strip())}"></div>'


def file_href(pr, path):
    """Deep link to a file in the PR's diff view; None if the host is unknown."""
    if not pr:
        return None
    pr = re.sub(r"/(diff|diffs|files|overview|commits|activity)/?$", "", pr.rstrip("/"))
    if "/pull/" in pr:  # GitHub
        return f"{pr}/files#diff-{hashlib.sha256(path.encode()).hexdigest()}"
    if "/merge_requests/" in pr:  # GitLab
        return f"{pr}/diffs#{hashlib.sha1(path.encode()).hexdigest()}"  # nosec B324
    if "bitbucket.org" in pr:  # Bitbucket Cloud
        return f"{pr}/diff#chg-{path}"
    if "/pull-requests/" in pr:  # Bitbucket Server / Data Center
        return f"{pr}/diff#{path}"
    return None


def file_row(f, note, pr):
    letter, label = STATUS.get(f["status"], STATUS["M"])
    folder, _, name = f["path"].rpartition("/")
    shown = (
        f'<span class="dir">{html.escape(folder)}/</span>' if folder else ""
    ) + f"<b>{html.escape(name)}</b>"
    href = file_href(pr, f["path"])
    if href:
        shown = f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{shown}</a>'
    if f["old"]:
        shown += f'<span class="from">from {html.escape(f["old"])}</span>'
    if note:
        shown += f'<span class="note">{md(note)}</span>'
    if f["add"] == "-":
        num = '<span class="n">binary</span>'
    else:
        num = f'<span class="n"><i class="a">+{f["add"]}</i> <i class="d">−{f["del"]}</i></span>'
    return (
        f'<li><span class="st st-{letter}" title="{label}">{letter}</span>'
        f'<span class="path">{shown}</span>{num}</li>'
    )


def totals(files):
    nums = [f for f in files if f["add"] != "-"]
    return sum(int(f["add"]) for f in nums), sum(int(f["del"]) for f in nums)


def words(text):
    return len((text or "").split())


def check_diagram(where, src):
    """Catch what breaks the page's look: hand-written colours, diamonds, sprawl."""
    errors = []
    body = re.sub(r'"[^"]*"', '""', src)  # ignore label text
    if re.search(r"^\s*(classDef|style|linkStyle)\b", body, re.M):
        errors.append(f"{where}: remove classDef/style lines; use :::add/:::chg/:::del")
    if re.search(r"\w\s*\{", body):
        errors.append(f"{where}: diamond node; use a rectangle and put the condition on the edge")
    body = re.sub(r"\|[^|]*\||:::\w+|\[[^\]]*\]|\([^)]*\)|\{[^}]*\}", " ", body)
    parent = {}

    def root(n):
        while parent[n] != n:
            n = parent[n]
        return n

    for line in body.splitlines()[1:]:  # first line is the flowchart header
        line = line.strip()
        if not line or re.match(r"(subgraph|end|direction|%%)\b", line):
            continue
        ids = re.findall(r"[A-Za-z_]\w*", ARROW.sub(" ", line))  # also handles A & B
        for n in ids:
            parent.setdefault(n, n)
        for a, b in zip(ids, ids[1:]):
            parent[root(a)] = root(b)
    if len(parent) > MAX_DIAGRAM_NODES:
        errors.append(f"{where}: {len(parent)} nodes, keep it to {MAX_DIAGRAM_NODES} or fewer")
    if len({root(n) for n in parent}) > 1:
        errors.append(f"{where}: separate fragments; draw one connected graph")
    return errors


def check_words(where, field, text):
    limit = WORD_LIMITS[field]
    n = words(text)
    return [f"{where}: {field} is {n} words, limit {limit}"] if n > limit else []


def validate(data):
    errors = check_words("title", "title", data.get("title"))
    ov = data.get("overview", {})
    errors += check_words("overview", "context", ov.get("context"))
    for b in ov.get("changes", []):
        errors += check_words("overview", "change", b)
    for key in ("before", "after", "around"):
        if ov.get(key):
            errors += check_diagram(f"overview.{key}", ov[key])
    for n, ch in enumerate(data["chapters"], 1):
        where = f"chapter {n}"
        errors += check_words(where, "summary", ch.get("summary"))
        names = len(re.findall(r"`[^`]+`", ch.get("summary") or ""))
        if names > MAX_SUMMARY_IDENTIFIERS:
            errors.append(
                f"{where}: summary names {names} identifiers, limit "
                f"{MAX_SUMMARY_IDENTIFIERS}; split the chapter"
            )
        errors += check_words(where, "look", ch.get("look"))
        for item in ch["files"]:
            if isinstance(item, dict):
                errors += check_words(f"{where} {item['path']}", "note", item.get("note"))
        for key in ("diagram", "before", "after"):
            if ch.get(key):
                errors += check_diagram(f"{where}.{key}", ch[key])
    return errors


def panes(spec):
    """Before/after (or a single diagram) as figure panes; '' if none."""
    before, after, around = spec.get("before"), spec.get("after"), spec.get("around")
    out = ""
    if before:
        out += f'<figure class="pane"><figcaption>Before</figcaption>{mermaid(before)}</figure>'
    if after:
        label = "After" if before else "What it adds"
        out += f'<figure class="pane"><figcaption>{label}</figcaption>{mermaid(after)}</figure>'
    if around:
        out += (
            f'<figure class="pane wide"><figcaption>Where it sits</figcaption>'
            f"{mermaid(around)}</figure>"
        )
    return f'<div class="ba">{out}</div>' if out else ""


def also_row(path, owner):
    name = html.escape(path.rsplit("/", 1)[-1])
    return (
        f'<li class="also"><span class="st">↗</span><span class="path">'
        f'<a href="#c{owner}"><b>{name}</b></a><span class="note">also part of this '
        f"story · its diff is reviewed in chapter {owner:02d}</span></span><span></span></li>"
    )


def chapter(i, ch, entries, pr, owners):
    rows = "".join(file_row(f, note, pr) for f, note in entries)
    rows += "".join(also_row(p, owners[p]) for p in ch.get("also", []))
    add, dele = totals([f for f, _ in entries])
    kind = html.escape(ch.get("kind", "logic"))
    if ch.get("before") or ch.get("after"):
        diagram = panes(ch)
    elif ch.get("diagram"):
        diagram = f'<div class="dia">{mermaid(ch["diagram"])}</div>'
    else:
        diagram = ""
    look = (
        f'<p class="look"><b>Look at</b><span>{md(ch["look"])}</span></p>'
        if ch.get("look")
        else ""
    )
    return (
        f'<section class="ch" id="c{i}"><header>'
        f'<span class="num">{i:02d}</span><h2>{md(ch["title"])}</h2>'
        f'<span class="kind">{kind}</span>'
        f'<label class="done"><input type="checkbox" id="done-{i}" data-ch="{i}">'
        f"Reviewed</label></header>"
        f'<p class="sum">{md(ch.get("summary", ""))}</p>{diagram}{look}'
        f'<div class="files"><div class="files-head"><span>{len(entries)} '
        f'file{"s" if len(entries) != 1 else ""} · review in this order</span>'
        f'<span class="n"><i class="a">+{add}</i> <i class="d">−{dele}</i></span></div>'
        f"<ul>{rows}</ul></div></section>"
    )


def map_item(i, ch, entries):
    shown = entries[:3]
    names = "".join(
        f"<li>{html.escape(f['path'].rsplit('/', 1)[-1])}</li>" for f, _ in shown
    )
    if len(entries) > len(shown):
        names += f'<li class="more">+{len(entries) - len(shown)} more</li>'
    return (
        f'<a class="m" href="#c{i}" data-map="{i}"><span class="num"><span>{i:02d}</span></span>'
        f'<span class="t">{md(ch["title"])}</span><ul>{names}</ul></a>'
    )


def build(data_path, out_path):
    data = json.loads(Path(data_path).read_text())
    rng = data["range"]
    files = {f["path"]: f for f in changed_files(rng)}
    kinds = {p: classify(p) for p in files}

    errors, owners, chapters = validate(data), {}, []
    for n, ch in enumerate(data["chapters"], 1):
        entries = []
        for item in ch["files"]:
            path, note = (item, None) if isinstance(item, str) else (item["path"], item.get("note"))
            if path not in files:
                errors.append(f"not in diff: {path}")
            elif path in owners:
                errors.append(f"in two chapters: {path} (use `also` for the second)")
            else:
                owners[path] = n
                entries.append((files[path], note))
        chapters.append((ch, entries))
    for n, ch in enumerate(data["chapters"], 1):
        for path in ch.get("also", []):
            if path not in owners or owners[path] == n:
                errors.append(f"chapter {n} also: {path} must be a file of another chapter")
    missing = [p for p, k in kinds.items() if k == "core" and p not in owners]
    if missing:
        errors.append("core files missing from chapters: " + ", ".join(missing))
    if errors:
        sys.exit("Fix data.json:\n  " + "\n  ".join(errors))

    tests = [(files[p], None) for p, k in kinds.items() if k == "test" and p not in owners]
    if tests:
        chapters.append(
            ({"title": "Tests", "kind": "tests",
              "summary": "Listed for completeness. These weren't read for this guide."},
             tests)
        )
    noise = [files[p] for p, k in kinds.items() if k == "noise"]

    pr = data.get("pr")
    ov = data.get("overview", {})
    bullets = "".join(f"<li>{md(b)}</li>" for b in ov.get("changes", []))
    add, dele = totals(list(files.values()))
    skipped = ""
    if noise:
        rows = "".join(f"<li>{html.escape(f['path'])}</li>" for f in noise)
        skipped = (
            f'<details class="skip"><summary>{len(noise)} generated or lock '
            f"files skipped</summary><ul>{rows}</ul></details>"
        )
    pr_link = (
        f'<a class="pr" href="{html.escape(pr)}" target="_blank" rel="noopener">Open PR ↗</a>'
        if pr
        else ""
    )
    overview_panes = panes(ov)
    legend = (
        '<p class="legend"><span><i class="lg add"></i>added</span>'
        '<span><i class="lg chg"></i>changed</span>'
        '<span><i class="lg del"></i>removed</span></p>'
    )

    page = TEMPLATE.read_text()
    fills = {
        "title": html.escape(data["title"]),
        "range": html.escape(rng),
        "meta": (
            f"{len(files)} files · <i class='a'>+{add}</i> <i class='d'>−{dele}</i>"
            f" · <code>{html.escape(rng)}</code>"
        ),
        "pr": pr_link,
        "context": md(ov.get("context", "")),
        "changes": f"<ul class='changes'>{bullets}</ul>" if bullets else "",
        "panes": overview_panes + legend if overview_panes else "",
        "map": "".join(map_item(i, ch, e) for i, (ch, e) in enumerate(chapters, 1)),
        "chapters": "".join(
            chapter(i, ch, e, pr, owners) for i, (ch, e) in enumerate(chapters, 1)
        ),
        "total": str(len(chapters)),
        "skipped": skipped,
    }
    for key, value in fills.items():
        page = page.replace("{{" + key + "}}", value)
    Path(out_path).write_text(page)
    print(f"Built {out_path}: {len(chapters)} chapters, {len(noise)} skipped files")


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["help"]
    if cmd == "collect":
        collect(*args)
    elif cmd == "diff" and len(args) >= 2:
        print(slim_diff(args[0], args[1:]), end="")
    elif cmd == "build" and len(args) == 2:
        build(*args)
    else:
        sys.exit(__doc__)
