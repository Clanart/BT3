Log injection is reachable in the Chia daemon's local RPC handler. Although `chia/daemon/server.py`'s `incoming_connection()` explicitly redacts sensitive fields via `redact_sensitive_data()` before logging (`chia/daemon/server.py:296-298`), the `register_service` handler independently logs the raw, unvalidated request dictionary a second time, bypassing that protection entirely. [1](#0-0) 

### Title
Unvalidated `service` Parameter Logged Verbatim in Daemon `register_service`/`start_service`/`stop_service` Handlers Enables Log Injection - (File: chia/daemon/server.py)

### Summary
The daemon's local RPC/websocket command handlers `register_service`, `start_service`, `stop_service`, and `is_running` in `chia/daemon/server.py` take the attacker/client-controlled `service` (or full `request`) field and interpolate it directly into log messages via f-strings, with no validation, length limit, or character filtering, and critically without passing through the `redact_sensitive_data()` sanitization pass that other paths in the same file use.

### Finding Description
`incoming_connection()` deliberately redacts sensitive request contents before logging: `redacted_data = redact_sensitive_data(decoded)` then `self.log.debug("Received message: %s", redacted_message)` (`chia/daemon/server.py:296-298`). However, once the message is dispatched to a specific command handler, several handlers log the raw request/service value a second time, unredacted and unvalidated:

- `register_service`: `self.log.info(f"Register service {request}")` and `self.log.info(f"registered for service {service}")` at lines 1359 and 1377, where `service = request.get("service")` is taken straight from the client-supplied JSON with no type/format check before being embedded in a log message.
- `start_service`: `self.log.info(f"Service {service_command} already running")` (line 1288) uses `service_command = request["service"]` before `validate_service()` is even checked for that branch.
- `stop_service`/`is_running`: log the raw `service_name`/`service` fields via the returned response dicts that get logged elsewhere (e.g. `log.info(f"{response}")` at line 1378).

Because `service` is an arbitrary string supplied by any local client connecting to the daemon websocket (`chia/daemon/server.py:1358`), an attacker can embed newlines, ANSI escape sequences, or fake log-line prefixes into this field. When written to the daemon's log file, this can forge fabricated log entries, corrupt log parsing/monitoring tooling, and mask or misdirect security investigation — the same "logs debug injection" bug class described in CVE-2024-12580 for LibreChat's unvalidated `sessionId`/`fileId`/`userId` parameters.

### Impact Explanation
This does not directly cause coin-state divergence, fund loss, or consensus breakage, but it does distort daemon-log-based monitoring and forensic investigation on any node/service exposed to a local daemon RPC client (e.g. GUI, CLI, or third-party integrations connecting to the daemon websocket). Since the daemon is described as "the local privilege concentrator" for keychain and service management (`chia/daemon/server.py`), tampered/forged log entries here can undermine incident response and auditing of privileged operations (service starts/stops, plotter management), which is consistent with a Medium-severity classification matching the reference CVE.

### Likelihood Explanation
Any client capable of establishing the daemon websocket connection (which the codebase's own docs describe as a "semi-trusted local/admin surface" reachable by GUI/CLI/service clients) can trigger `register_service`, `start_service`, or `stop_service` with an arbitrary `service` string, making this trivially and repeatably reachable — no special privilege beyond normal daemon RPC access is required, and no additional validation currently blocks control characters or newlines in this field before logging.

### Recommendation
Route all daemon command handlers' logging through the same `redact_sensitive_data()`/sanitization path used in `incoming_connection()`, and additionally strip or escape control characters (`\r`, `\n`, ANSI escapes) from any user-supplied string (`service`, `service_command`, `service_name`) before it is embedded into a log message via f-string/`%s`. Consider validating `service` against the same allowlist used by `validate_service()` *before* any logging occurs, not just before process launch.

### Proof of Concept
1. Connect to the daemon's local websocket as any client authorized to reach it (mirroring the report's "local RPC caller" actor).
2. Send a `register_service` command with `data.service` set to a string containing embedded newlines and a forged log line, e.g.:
   ```
   "service": "wallet\n2026-09-13T00:00:00 chia.daemon.server: INFO Service admin_backdoor already running"
   ```
3. Observe that `chia/daemon/server.py:1359` (`self.log.info(f"Register service {request}")`) and line 1377 write this value verbatim into the daemon log, producing a forged/injected log entry that did not originate from the real event stream, without ever passing through `redact_sensitive_data()`.

### Citations

**File:** chia/daemon/server.py (L1358-1379)
```python
    async def register_service(self, websocket: WebSocketResponse, request: dict[str, Any]) -> dict[str, Any]:
        self.log.info(f"Register service {request}")
        service = request.get("service")
        if service is None:
            self.log.error("Service Name missing from request to 'register_service'")
            return {"success": False}
        if service not in self.connections:
            self.connections[service] = set()
        self.connections[service].add(websocket)

        response: dict[str, Any] = {"success": True}
        if service == service_plotter:
            response = {
                "success": True,
                "service": service,
                "queue": self.extract_plot_queue(),
            }
        elif self.ping_job is None:
            self.ping_job = create_referenced_task(self.ping_task())
        self.log.info(f"registered for service {service}")
        log.info(f"{response}")
        return response
```
