# last9-db

Fetch Last9 database metrics for a service or database: slow queries (including MongoDB slow logs from `*_mongo` services), query performance stats, server-level metrics (connections, replication lag, I/O), and available databases.

## Usage

`/last9-db [service_or_db_name] [time window]`

Examples:
- `/last9-db unified-cart-sanic last 1 hour`
- `/last9-db orders-db last 30 minutes`
- `/last9-db payment-engine-sanic 2026-04-18T00:00:00Z to 2026-04-18T01:00:00Z`
- `/last9-db poseidon_mongo last 30 minutes`  â† direct MongoDB slow log fetch
- `/last9-db last 1 hour`  â† no service: show all DBs + all *_mongo slow logs

## Behavior

### Step 1 â€” Parse arguments
- `service_or_db`: either a service name (to find its associated DBs), a `*_mongo` log service name, or a direct DB identifier. If omitted, fetch all.
- `time_window`: relative â†’ `lookback_minutes`, or absolute ISO8601 start/end

### Step 2 â€” Load tools
Use `ToolSearch` with:
```
select:mcp__claude_ai_Last9__get_databases,mcp__claude_ai_Last9__get_database_slow_queries,mcp__claude_ai_Last9__get_database_queries,mcp__claude_ai_Last9__get_database_server_metrics,mcp__claude_ai_Last9__get_service_logs,mcp__claude_ai_Last9__get_logs,mcp__claude_ai_Last9__did_you_mean,mcp__claude_ai_Last9__get_service_environments
```

### Step 2.5 â€” Resolve service name and environment
If a `service_name` was provided, run simultaneously:
1. `mcp__claude_ai_Last9__did_you_mean` â€” confirm exact service name spelling (warn if < 80% match)
2. `mcp__claude_ai_Last9__get_service_environments` â€” validate env; omit if user's env not found

### Step 3 â€” Discover databases and *_mongo services (in parallel)

Run both simultaneously:

1. **Trace-based DB discovery** â€” `mcp__claude_ai_Last9__get_databases`
   - Lists all databases detected from OTel trace spans with their throughput, p95 latency, error rate, and service count.
   - If user passed a service/db name, filter results to matching entries.

2. **MongoDB log service discovery** â€” `mcp__claude_ai_Last9__get_logs` with:
   ```json
   [{"type": "filter", "sql": "ServiceName LIKE '%_mongo%'"}]
   ```
   with `limit: 5` to discover which `*_mongo` services are active.
   - Known `*_mongo` services (as of discovery): `cohorting_mongo`, `poseidon_mongo`, `post_mongo`, `cerberus_mongo`, `vitality_mongo`
   - If user specified a `*_mongo` service name directly, skip discovery and go straight to Step 4b.

### Step 4a â€” Fetch trace-based DB metrics (in parallel)
For each relevant database from Step 3, fire simultaneously:

1. **Slow queries from traces** â€” `mcp__claude_ai_Last9__get_database_slow_queries`
   - `min_duration_ms`: 100 (default), `limit`: 20
   - Returns actual observed slow query spans with trace IDs

2. **Query performance stats** â€” `mcp__claude_ai_Last9__get_database_queries`
   - `db_system`: postgresql / mysql / mongodb / redis as appropriate
   - `sort_by`: latency
   - Returns top query patterns by avg/p95 latency and call count

3. **Server metrics** â€” `mcp__claude_ai_Last9__get_database_server_metrics`
   - Discovers all exporters (postgres, redis, mongodb, elasticsearch, etc.) and returns connection counts, cache hit rate, replication lag, blocked clients

### Step 4b â€” Fetch MongoDB slow logs from *_mongo services (in parallel)

MongoDB clusters run in two log formats depending on version:
- **MongoDB 5.x+ (structured JSON)**: log lines have `msg: "Slow query"` â€” use `mcp__claude_ai_Last9__get_service_logs` with `body_filters: ["Slow query"]`
- **MongoDB 4.x (text format)**: slow queries are inline text ending in `Nms` with no `"Slow query"` keyword â€” use `mcp__claude_ai_Last9__get_logs` with `logjson_query: [{"type": "filter", "sql": "ServiceName = '<service>_mongo'"}]` instead

**Always try both approaches per service** â€” if `get_service_logs` with `body_filters: ["Slow query"]` returns 0 results, fall back to `get_logs` with the SQL filter to catch 4.x text-format slow logs.

Known format per service (as of 2026-04-18):
- MongoDB 5.x (structured JSON): `cohorting_mongo`, `poseidon_mongo`, `post_mongo`, `cerberus_mongo`, `vitality_mongo`
- MongoDB 4.x (text format): `subscription_mongo`

For 5.x services, call `mcp__claude_ai_Last9__get_service_logs` simultaneously:
```
service: <name>_mongo
body_filters: ["Slow query"]
lookback_minutes: <from time_window>
limit: 20
```

For 4.x services (or as fallback), call `mcp__claude_ai_Last9__get_logs`:
```json
[{"type": "filter", "sql": "ServiceName = '<name>_mongo'"}]
```
with `lookback_minutes` and `limit: 20`.

Parse each slow query log entry (structured JSON in the `message` field) and extract:
- `ns`: namespace (db.collection)
- `durationMillis`: query duration
- `planSummary`: execution plan (COLLSCAN = missing index âš ï¸, IXSCAN = index used)
- `keysExamined` / `docsExamined`: scan efficiency (high ratio = poor index selectivity)
- `nreturned`: documents returned
- `command.find` or `command.aggregate`: operation type and collection
- `command.filter`: query predicate (summarize, don't dump full JSON)
- `errName` / `errMsg`: if present (e.g. `MaxTimeMSExpired` = query timed out âš ï¸)
- `hasSortStage`: true = in-memory sort, potential missing sort index

### Step 5 â€” Present results

#### Databases Found (trace-based)
Table: db_system | host | throughput (rpm) | p95 latency | error rate | services using it
- Flag any DB with p95 > 10s or error_rate > 0

#### Slow Queries (from traces)
Table: query | db | duration | service | timestamp
- Flag any with duration > 1s or `MaxTimeMSExpired`

#### Top Queries by Execution Time (trace-based)
Table: query pattern | db_system | avg latency | p95 latency | calls/min | errors

#### MongoDB Slow Logs (from *_mongo services)

For each `*_mongo` service with results:

**`<service>_mongo`** â€” N slow queries in last X min

| Collection | Operation | Duration | Plan | Keys Examined | Docs Examined | Returned | Error |
|---|---|---|---|---|---|---|---|

Flags per service:
- `COLLSCAN` with large `docsExamined` â†’ missing index on filter field
- High `keysExamined` / `nreturned` ratio (e.g. 9382 examined, 0 returned) â†’ poor index selectivity, needs compound index
- `hasSortStage: true` with large scan â†’ missing sort index
- `MaxTimeMSExpired` â†’ query hitting client-set timeout, needs immediate attention
- Same query pattern repeating â†’ persistent unresolved bottleneck

#### Server Metrics
| System | Metric | Value | Status |
|---|---|---|---|
| Redis | Blocked clients | X | ðŸ”´ if > 0 |
| Redis | Connected clients | X | |
| Elasticsearch | Cluster health | green/yellow/red | |
| Elasticsearch | JVM heap % | X% | ðŸ”´ if > 85% |

#### Observations & Flags
- Flag MongoDB COLLSCAN on large collections (>10k docs)
- Flag keysExamined/nreturned ratio > 100:1 (index not selective enough)
- Flag `MaxTimeMSExpired` queries â€” client timeout hit, query too slow
- Flag Redis blocked clients > 0 (BLPOP/BRPOP consumers missing or lagging)
- Flag Redis p95 > 10s (likely blocked on queue with no consumer)
- Flag PostgreSQL queries with avg > 1s at > 10 rpm
- Flag Elasticsearch error rate > 0
- Flag connection utilization > 80%
- Flag cache hit rate < 90%

## Notes
- MongoDB slow logs are written by the MongoDB server itself (not OTel) and are NOT in the trace-based `get_database_slow_queries` tool.
- **MongoDB 5.x**: logs have `msg: "Slow query"` and `slow_query: "true"` label â€” use `get_service_logs` with `body_filters: ["Slow query"]`.
- **MongoDB 4.x** (`subscription_mongo` and similar): slow queries are text-format lines ending in `Nms` with fields like `keysExamined:N docsExamined:N nreturned:N`. No `"Slow query"` keyword. Use `get_logs` with SQL filter `ServiceName = '<name>_mongo'`. Parse fields: duration from trailing `Nms`, `planSummary:`, `keysExamined:`, `docsExamined:`, `nreturned:`, `replanned:`, `replanReason:`.
- `replanned: 1` with `replanReason: "cached plan was less efficient than expected"` = MongoDB keeps re-evaluating the query plan because it's underperforming. Strong signal that the current index is too broad â€” need a more selective compound index.
- Large `reslen` values (e.g. 875KB per batch) indicate large document payloads â€” consider projection to limit returned fields.
- The `get_logs` SQL filter `ServiceName LIKE '%_mongo%'` discovers all active `*_mongo` log services.
- MongoDB log `durationMillis` (5.x) or trailing `Nms` (4.x) is the server-measured execution time. Slow query threshold is typically 100ms.
- `COLLSCAN` always means a missing or unusable index â€” recommend adding one on the filter field.
- Default `lookback_minutes` is 60 if not specified.
- Query predicates in MongoDB logs may contain user IDs â€” summarize the shape, not the literal values.
