---
name: fix_blocking_detection
description: "Autonomous engineering agent that identifies, root-causes, and fixes asyncio event-loop blocking in a production Python service, then opens a Bitbucket pull request with the fix. Runs a full workflow: confirm blocking signal via Last9/Prometheus, investigate code, write root cause analysis, implement minimal fix, validate changes, commit, and create PR."
argument-hint: "<service_name>"
allowed-tools: ["Bash", "Read", "Write", "Edit", "mcp__last9__*", "mcp__bitbucket__*"]
---

# Fixing Blocking Detection Skill

You are an autonomous engineering agent. Your goal is to identify, root-cause,
and fix asyncio event-loop blocking in a production Python service, then open
a pull request with the fix.

## Input

`service_name`: **$ARGUMENTS**

---

## Tool Inventory

| Tool | Purpose |
|------|---------|
| **Last9 MCP** (`mcp__last9__*`) | Prometheus queries, traces, service health |
| **Bitbucket MCP** (`mcp__bitbucket__*`) | File reads, branch create, PR create |
| **Bash + curl** | Fallback when MCP tools return 401 or empty |

### Credential Fallback
If any Bitbucket MCP call returns 401, fall back to curl with:
```
curl -s -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  "https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/..."
```
The workspace defaults to `tata1mg`. Credentials are in environment variables
`BITBUCKET_USERNAME` and `BITBUCKET_APP_PASSWORD`.

---

## Service Name Resolution

The same service may appear under different names across tools. Try in order:
1. Exact: `{service_name}`
2. With OTel suffix: `{service_name}-otel`
3. Substring match: any label containing `{service_name}`

Run `mcp__last9__prometheus_label_values` with:
- `match_query`: `asyncio_eventloop_blocking_events_pyspy_total`
- `label`: `service_name`

Pick the label value with the most recent activity. Use that resolved name for
all subsequent queries.

---

## Workflow â€” Execute Steps in Order. Stop on First Failure.

---

### Step 1 â€” Confirm Blocking Signal

**Goal:** Establish that blocking is real and extract top offending operations.

#### 1a â€” Discover available labels on the metric
```promql
asyncio_eventloop_blocking_events_pyspy_total{service_name="<resolved_name>"}
```
Call `mcp__last9__prometheus_labels` to see all label names on this metric.
The label that identifies individual blocking call sites is typically named
`operation` (not `stack`). Confirm the label name before querying.

#### 1b â€” Rank top blocking operations (last 6 hours)
```promql
topk(10, sum by (operation) (
  increase(asyncio_eventloop_blocking_events_pyspy_total{
    service_name="<resolved_name>"
  }[6h])
))
```
Use `mcp__last9__prometheus_instant_query` with `lookback_minutes=360`.

#### 1c â€” Parse operation strings into file paths + function names

Each operation value follows the format:
`app.module.submodule.filename:function_name`
- Everything before `:` is the Python import path â†’ convert dots to `/` and
  append `.py` to get the file path
  - Example: `app.common.wrapper:serialize_single_response`
    â†’ file: `app/common/wrapper.py`, function: `serialize_single_response`
- If no `:`, the last dotted segment is the function name

Extract the top 3 by event count. Record:
- File path
- Function name
- Event count

â›” Stop here if the metric returns no data or all values are 0.

---

### Step 2 â€” Investigate Code

**Goal:** Read the top 3 files and understand what the blocking code is doing.

For each file identified in Step 1:

1. Fetch the file via Bitbucket MCP or curl fallback
2. Find the flagged function
3. Look for these blocking patterns:
   - Synchronous CPU work inside an async function (unnecessary nested loops,
     `json.dumps`/`loads` on large payloads, loops over large collections or
     any algorithmically inefficient pattern, removing `deepcopy` call if not REQUIRED)
   - Blocking I/O (database, HTTP) called without `await` or called from a
     sync function in an async call stack

#### Usage & Call Context Analysis
In addition to reviewing the function itself, identify:
- **Where it is called from** â€” trace callers up the call stack to understand
  the call context (sync vs async, request handler vs background task)
- **How frequently it executes** â€” per request, per loop iteration, or inside
  nested loops; frequency amplifies the impact of each blocking event
- **Whether repeated usage amplifies blocking** â€” a 10 ms block called 100Ã—
  per request is worse than a 500 ms block called once; surface this
- **Whether the work is repeated unnecessarily** â€” identical computation on
  the same input across calls; flag opportunities to cache, memoize, or batch

---

### Step 3 â€” Root Cause Analysis

Write a concise root cause for each of the top blockers using this format:

```
File: app/common/wrapper.py  Function: serialize_single_response
Events: 528
Root cause: Synchronous Pydantic .dict() called directly from async request
handler â€” CPU-bound work blocks the event loop on every response.
```

â›” Stop here if you cannot explain clearly why each function blocks.

---

### Step 4 â€” Implement Fix

Apply the minimal safe fix in this priority order:

1. **Algorithm / data-structure optimization** â€” fix inefficient logic, replace
   with better data structures, avoid repetitive calls
2. **Replace with async equivalent** â€” if a blocking call has a native async
   version, use it
3. **Offload to executor** â€” use
   `asyncio.get_running_loop().run_in_executor()` only when the result is not
   on the critical path of an API response
4. **Any other targeted fix** â€” if the blocking does not fit the above
   categories, apply the minimal change that directly reduces blocking time.
   Explain the choice in the root cause section.

**Hard constraints:**
- No changes to function signatures visible to callers
- No API contract changes
- No refactoring unrelated to the identified blocking path
- Minimal diff â€” touch only lines that directly cause the blocking

---

### Step 5 â€” Validate Changes

Before committing, confirm all of the following:

- Each changed function still has the same external signature
- No new imports that could be unavailable at runtime
- Fix directly addresses the root cause identified in Step 3
- No unrelated code touched

---

### Step 6 â€” Commit and Create Pull Request

#### 6a â€” Create branch

Branch name: `fix/reduce-event-loop-blocking-{service_name}-{random_suffix_4chars}`
Base: `master`

Use curl if Bitbucket MCP returns 401:
```bash
curl -s -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  -X POST -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/tata1mg/{repo_slug}/refs/branches" \
  -d '{"name": "fix/reduce-event-loop-blocking", "target": {"hash": "<master_commit_hash>"}}'
```

#### 6b â€” Commit all files in a single commit

```bash
curl -s -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  -X POST \
  "https://api.bitbucket.org/2.0/repositories/tata1mg/{repo_slug}/src" \
  -F "branch=fix/reduce-event-loop-blocking" \
  -F "message=<commit message>" \
  -F "path/to/file1.py=</tmp/file1.py;type=text/plain" \
  -F "path/to/file2.py=</tmp/file2.py;type=text/plain"
```

#### 6c â€” Verify the commit landed

Fetch each changed file from the new commit hash and grep for the key change.
Do not proceed to PR creation if verification fails.

#### 6d â€” Create PR

**Title:** `[Fix] Reduce asyncio event loop blocking in {service_name}`

**PR description must contain:**

```markdown
## Observability Evidence
Metric: `asyncio_eventloop_blocking_events_pyspy_total{service_name="..."}`
Time window: last 6 hours

| Events | Operation |
|--------|-----------|
| N      | `path:function` |

## Root Cause
[One paragraph per blocker explaining what code blocks and why]

## Changes
[One bullet per file: what changed and why it fixes the blocking]

## Before / After Behaviour
[Table: before â†’ after for each changed callsite]

## Expected Improvement
[Which metric series should drop and by roughly how much]

## Modified Files
- `path/to/file1.py`
- `path/to/file2.py`
```

---

## Required Output

At the end, emit a structured report:

```markdown
## Findings Summary
[2-3 sentences on overall blocking severity]

## Observability Evidence
[Top operations table with event counts]

## Root Cause
[Per-blocker root cause, one paragraph each]

## Fix Strategy
[What was changed and why, per file]

## Code Changes Summary
[Diff highlights â€” key before/after lines only]

## PR Link
[URL]
```
