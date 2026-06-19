# last9-dig-deeper

Orchestrates a full-stack root-cause investigation for a service experiencing latency, errors, or degradation. Combines APM, traces, dependency graph, Kubernetes infra, database slow queries, logs, and change events into a unified diagnosis â€” including recursive downstream investigation.

## Usage

`/last9-dig-deeper [service] [symptom] [time window]`

Examples:
- `/last9-dig-deeper unified-cart-sanic high latency last 1 hour`
- `/last9-dig-deeper ads-service-sanic error spike last 30 minutes`
- `/last9-dig-deeper payment-engine-sanic degradation 2026-04-18T00:00:00Z to 2026-04-18T01:00:00Z`

## Behavior

### Step 1 â€” Parse arguments
- `service_name`: the primary service under investigation
- `symptom`: one of `high latency`, `error spike`, `degradation`, `crash`, or freeform description
- `time_window`: relative â†’ `lookback_minutes`; or absolute ISO8601 â†’ derive `lookback_minutes` for Prometheus tools (see Notes)
- `env` (optional): if the user specifies one, use it. Otherwise, **omit env on first attempt** (returns all envs). Only fall back to `production` if results are empty. `get_service_dependency_graph` requires env â€” use `production` unless a different env is known.

> **Prometheus time limitation**: `prometheus_instant_query` and `prometheus_range_query` silently return `[]` for historical `time_iso`/`start_time_iso`/`end_time_iso`. For all Prometheus queries (infra items 6â€“11 in Step 3 and Step 4 follow-ups), always use `lookback_minutes`. For absolute input times, compute `lookback_minutes = ceil((now - window_start) / 60)`. APM and log tools (`get_service_performance_details`, `get_service_logs`, etc.) support both formats.

### Step 2 â€” Load all required tools
Use `ToolSearch` with:
```
select:mcp__claude_ai_Last9__get_service_performance_details,mcp__claude_ai_Last9__get_service_traces,mcp__claude_ai_Last9__get_service_dependency_graph,mcp__claude_ai_Last9__get_service_logs,mcp__claude_ai_Last9__get_exceptions,mcp__claude_ai_Last9__get_databases,mcp__claude_ai_Last9__get_database_slow_queries,mcp__claude_ai_Last9__get_database_queries,mcp__claude_ai_Last9__prometheus_instant_query,mcp__claude_ai_Last9__prometheus_range_query,mcp__claude_ai_Last9__prometheus_label_values,mcp__claude_ai_Last9__did_you_mean,mcp__claude_ai_Last9__get_change_events
```

### Step 2.5 â€” Resolve service name (before any data fetch)
**Primary resolver** â€” call `mcp__claude_ai_Last9__prometheus_label_values` with:
- `match_query`: `trace_endpoint_count`
- `label`: `service_name`
- `lookback_minutes`: 60

Check if the user's input appears **exactly** in the returned list.
- **Exact match found** â†’ use the name as-is. Proceed to Step 3.
- **Not found** â†’ fall back to `mcp__claude_ai_Last9__did_you_mean`. If the top fuzzy match is â‰¥ 80% similar, use it and inform the user. If < 80%, warn the user and ask to confirm before proceeding.

**Env resolution**:
- If the user specifies an env, use it for all calls.
- Otherwise **omit env** by default (returns all envs). `get_service_dependency_graph` is the only exception â€” it requires env; default to `production` unless the user specified otherwise.
- Do NOT call `get_service_environments` unless you need to validate a user-supplied env.

**Downstream service name resolution** (Step 4): For downstream targets discovered from CLIENT span hostnames (e.g. `search-service-sanic.prod.svc.cluster.local`), first check the `prometheus_label_values` list from this step before calling `did_you_mean` â€” the hostname prefix often matches the APM service name directly.

Only proceed to Step 3 once the service name is confirmed.

### Step 3 â€” Broad data collection (all in parallel)

Fire all of the following simultaneously:

#### APM Layer
1. `mcp__claude_ai_Last9__get_service_performance_details` â€” throughput, error rate, p50/p90/p95, apdex, top slow ops, top errors
2. `mcp__claude_ai_Last9__get_service_traces` with `limit=10` â€” slowest traces for span-level breakdown. **Use a Â±15 min window around the event**, not just the exact spike minutes â€” traces are sparse and a narrow window often returns 0.
3. `mcp__claude_ai_Last9__get_service_dependency_graph` â€” upstream/downstream dependency health

#### Log & Exception Layer
4. `mcp__claude_ai_Last9__get_service_logs` â€” error/warn logs in the time window (severity_filters: ["error","warn"], limit: 50). **If 0 results, retry without severity_filters** â€” some services log everything at the same level.
5. `mcp__claude_ai_Last9__get_exceptions` â€” exception count and types for the service

#### Change Events
6. `mcp__claude_ai_Last9__get_change_events` â€” fetch all change events in the Â±30 min window around the spike. No `event_name` filter on the first call to discover available types. A deployment or config change coinciding with the spike is often the root cause.

#### Infrastructure Layer (Kubernetes)
7. CPU usage per pod:
   ```promql
   avg(rate(container_cpu_usage_seconds_total{pod=~"<service>.*", container!=""}[5m])) by (pod)
   ```
8. CPU limits per pod:
   ```promql
   avg(kube_pod_container_resource_limits{pod=~"<service>.*", resource="cpu"}) by (pod)
   ```
9. Memory usage per pod:
   ```promql
   avg(container_memory_working_set_bytes{pod=~"<service>.*", container!=""}) by (pod)
   ```
10. Memory limits per pod:
    ```promql
    avg(kube_pod_container_resource_limits{pod=~"<service>.*", resource="memory"}) by (pod)
    ```
11. Pod restarts:
    ```promql
    sum(kube_pod_container_status_restarts_total{pod=~"<service>.*"}) by (pod, container)
    ```
12. Running pod count:
    ```promql
    count(kube_pod_status_phase{pod=~"<service>.*", phase="Running"})
    ```

#### Database Layer
13. `mcp__claude_ai_Last9__get_databases` â€” discover associated databases
14. `mcp__claude_ai_Last9__get_database_slow_queries` â€” slow queries in the window (run after DB discovery if needed)
15. MongoDB slow logs via `mcp__claude_ai_Last9__get_service_logs` for any `*_mongo` service associated with the service under investigation. Known services: `cohorting_mongo`, `poseidon_mongo`, `post_mongo`, `cerberus_mongo`, `vitality_mongo`. Use `body_filters: ["Slow query"]`, `limit: 10`. Parse `durationMillis`, `planSummary`, `keysExamined`, `docsExamined`, `nreturned`, `errName` from the JSON message body.

### Step 4 â€” Second-level investigation (based on findings)

After analyzing Step 3 results, perform targeted follow-up **only for confirmed hot spots**. All follow-up queries for different hot spots should be fired in parallel.

#### Downstream Service Deep Dive
When traces show slow CLIENT spans to internal services, investigate each confirmed hot downstream:

- **Resolve correct service name first**: Always call `mcp__claude_ai_Last9__did_you_mean` for each downstream target hostname (e.g. `diagnostics-k8s.prod.svc.cluster.local` â†’ query `diagnostics-k8s`). K8s service hostnames often differ from Last9 service names.
- **Fetch APM + traces + logs in parallel** for each resolved downstream service name
- **Fetch K8s infra for each downstream** using the pod name prefix (the pod prefix may differ from the service name â€” use the `did_you_mean` result or derive from hostname):
  - Pod count: `count(kube_pod_status_phase{pod=~"<downstream>.*", phase="Running"})`
  - CPU: `avg(rate(container_cpu_usage_seconds_total{pod=~"<downstream>.*", container!=""}[5m])) by (pod)`
  - CPU limits: `avg(kube_pod_container_resource_limits{pod=~"<downstream>.*", resource="cpu"}) by (pod)`
  - Memory: `avg(container_memory_working_set_bytes{pod=~"<downstream>.*", container!=""}) by (pod)`
  - Memory limits: `avg(kube_pod_container_resource_limits{pod=~"<downstream>.*", resource="memory"}) by (pod)`
  - Restarts: `sum(kube_pod_container_status_restarts_total{pod=~"<downstream>.*"}) by (pod, container)`

#### Handling the Instrumentation Gap
When a downstream service returns null APM and empty traces/logs (common for services that are only observable as CLIENT spans):
1. Try `did_you_mean` to find alternate service name in Last9
2. Retry logs/traces with resolved name
3. If still empty: **the service has no server-side observability in Last9** â€” document this explicitly and pivot fully to K8s infra signals
4. **Flag pod count â‰¤ 2 as a single-point-of-failure risk** â€” one pod restart eliminates 50%+ capacity

#### Multi-Service Simultaneous Degradation Pattern
If 3+ downstream services degrade concurrently with no matching change event:
- This strongly indicates a **shared infrastructure event**: network blip, compute node degradation, service mesh/load balancer issue, or shared upstream dependency (Redis, DB, DNS)
- Check if error types are connection-layer (`ServerDisconnectedError`, `ConnectionResetError`) vs application-layer â€” connection-layer errors across services = network/infra event
- Check change events with a wider window (Â±2 hours)
- Note: no remediation action may be needed if it was transient and self-resolved

#### Other follow-up triggers
- **If a downstream service shows high latency/errors in dependency graph**: call `mcp__claude_ai_Last9__get_service_performance_details` for that downstream service
- **If traces show slow CLIENT spans to external systems** (SQS, Redis, external HTTP): note the target host and flag as a likely bottleneck; if it's an internal service, fetch its APM too
- **If DB slow queries found**: call `mcp__claude_ai_Last9__get_database_queries` for full query stats
- **If CPU > 80% of limit**: fetch CPU trend range query to determine if it's a spike or sustained:
  ```promql
  sum(rate(container_cpu_usage_seconds_total{pod=~"<service>.*", container!=""}[5m])) by (pod)
  ```
- **If restarts > 0**: fetch termination reason (covers both OOMKill and Error exits):
  ```promql
  sum(kube_pod_container_status_last_terminated_reason{pod=~"<service>.*"}) by (pod, reason)
  ```
- **If error logs found**: search for the specific error pattern more deeply with `mcp__claude_ai_Last9__get_logs` using a targeted `logjson_query`

### Step 5 â€” Synthesize and present findings

Structure the output as follows:

---

## Root Cause Analysis: `<service>` â€” `<symptom>`
**Time window**: <start> â†’ <end>  **Environment**: <env>

### TL;DR â€” Most Likely Causes
Numbered list of the top 2-3 hypotheses, ranked by confidence. Be specific:
- e.g. "SQS calls to `sqs.ap-south-1.amazonaws.com` timing out at 5s (2 traces, exact 5000ms duration suggests client timeout config)"
- e.g. "Downstream `search-service` responding in 1.4â€“2s on CLIENT spans, contributing to `GET /v2/carts` total latency"
- e.g. "Transient infra event: 5 services simultaneously hit connection errors (ServerDisconnected, ConnectionReset) with no deployment in the window â€” indicates a network or node-level blip"

### APM Summary
| Metric | Value | Status |
|---|---|---|
| Throughput | X rpm | |
| Error Rate | X rpm (X%) | ðŸ”´/ðŸŸ¡/ðŸŸ¢ |
| P95 Latency | Xms | ðŸ”´/ðŸŸ¡/ðŸŸ¢ |
| Apdex | X.XX | ðŸ”´/ðŸŸ¡/ðŸŸ¢ |

### Slow Operations
Table: operation | p95 | throughput | flag

### Dependency Health
Table: dependency | direction | observed latency | pod count | restarts | server-side observability | flag

### Infrastructure (primary + all investigated downstreams)
| Service | Pod | CPU % | Mem % | Restarts | Termination Reason | Status |
|---|---|---|---|---|---|---|

### Change Events
- List any deployments/config changes found near the spike window, or "No change events detected in Â±30 min window"

### Database
- Slow queries table if found, else "No slow queries detected"

### Error Logs
- Top 5 distinct error patterns with count and sample message

### Observability Gaps
- List any downstream services that had no server-side APM/logs/traces, and note what signals were used instead

### Recommended Actions
Bulleted list of concrete next steps, ordered by impact:
- e.g. "Increase SQS client timeout or add circuit breaker for queue operations"
- e.g. "Investigate `search-service` â€” its p95 is contributing X% of cart latency"
- e.g. "Scale up `unified-cart-sanic` â€” pod `whj5l` at 43% CPU with upward trend"
- e.g. "Add server-side APM instrumentation to `diagnostics-k8s` â€” currently a black box with only 2 pods"

---

## Flags and Thresholds Used
| Signal | Threshold | Triggered? |
|---|---|---|
| High latency | p95 > 500ms | Yes/No |
| Error rate | > 1% of throughput | Yes/No |
| CPU pressure | > 80% of limit | Yes/No |
| Memory pressure | > 80% of limit | Yes/No |
| Pod restarts | > 0 | Yes/No |
| Slow DB query | avg > 1s | Yes/No |
| Downstream degraded | p95 > 500ms or error > 0 | Yes/No |
| Low pod count | â‰¤ 2 pods | Yes/No |
| Concurrent multi-service degradation | â‰¥ 3 services simultaneously | Yes/No |
| Change event near spike | within Â±30 min | Yes/No |

## Notes
- Always run Step 3 fully in parallel before Step 4 to minimize total wall-clock time.
- In Step 4, fire all downstream investigations in parallel â€” don't wait for one before starting the next.
- Trace durations are in nanoseconds â€” convert to ms (divide by 1,000,000) for all output.
- Memory values are bytes â€” convert to MB for display.
- CPU usage is in cores â€” show as % of limit.
- Default `lookback_minutes` is 60 if not specified.
- **Widen the trace window to Â±15 min around the event** â€” a 3-minute window for a sparse service returns 0 traces.
- If the user mentions a specific trace ID or error message, include it as a seed for targeted log/trace lookup.
- The goal is a single, actionable diagnosis â€” not a data dump. Always end with ranked hypotheses and concrete next steps.
- **Prometheus tools only accept `lookback_minutes`** â€” `time_iso` and `start_time_iso`/`end_time_iso` silently return `[]`. For absolute input windows, compute `lookback_minutes = ceil((now - window_start) / 60)` for all infra PromQL queries. APM and log tools support both formats.
- **APM throughput/response_times may be null** even when the service is active (instrumentation gap in some services). Use apdex and trace data as the primary health signals when this happens.
- **K8s pod name prefix â‰  Last9 service name**: always use `did_you_mean` before querying a downstream service by name. The CLIENT span target hostname (e.g. `diagnostics-k8s.prod.svc.cluster.local`) gives you the K8s service name; the Last9 service name may differ.
- **Connection-layer errors = infra signal**: `ServerDisconnectedError`, `ConnectionResetError`, `asyncio.TimeoutError` in aiohttp all indicate the TCP connection failed before a response arrived â€” these point to network, pod crash, or load balancer issues, not application bugs.
- **Always check change events** â€” a deployment within Â±30 min of a spike is the most common root cause and the quickest to confirm or rule out.
