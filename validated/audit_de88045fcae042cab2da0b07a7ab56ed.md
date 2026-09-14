### Title
Daemon `register_service` allows any local daemon client to claim an arbitrary/privileged service identity, enabling cross-service message interception - ([File: chia/daemon/server.py])

### Summary
`WebSocketServer.register_service()` in `chia/daemon/server.py` accepts any client-supplied `service` string and adds the connecting websocket to `self.connections[service]` with no check that the caller is authorized to claim that particular service identity. [1](#0-0)  Unlike `start_service`, which validates the requested name via `validate_service()` before launching a child process, `register_service` performs no equivalent allowlist or ownership check on the `service` name a websocket registers under. [2](#0-1) 

### Finding Description
The daemon is a local privilege concentrator: any TLS-authenticated client (CLI, GUI, wallet RPC, full-node RPC, plotter, etc.) that connects to the daemon websocket can call `register_service` with any service name, including the names used by other, more privileged services (e.g. `chia_wallet`, `chia_full_node`, `wallet_ui`). [3](#0-2) [4](#0-3)  Because destination-based message forwarding treats "registered under a service name" as full routing authority — the daemon serializes and sends any message whose `destination` matches a registered service name to **all** websockets registered under that name, without further interpreting or authenticating the payload — a client that registers itself under someone else's service name will silently receive a copy of every message intended for the legitimate service. [5](#0-4)  The registration table also collapses duplicate registrations by websocket identity only, so nothing prevents a second, unrelated connection from registering under an already-in-use service name. [6](#0-5) 

This mirrors the CVE's bug class: a party with only baseline local access (a valid daemon client, analogous to "an attacker with a basic level of access to the cluster") can assert an identity/namespace ("service" name, analogous to the Kubernetes namespace) it is not authorized to hold, and thereby gain access to traffic/data meant for a more privileged principal in that namespace.

### Impact Explanation
Any process holding daemon client credentials (which, per the module's own docs, is a "semi-trusted local/admin surface" rather than a hardened authorization boundary) can hijack forwarding meant for another registered service such as the wallet or full-node RPC bridge, intercepting responses/requests relayed through the daemon (state-change notifications, RPC command results forwarded via the daemon websocket, etc.). [7](#0-6)  This is a confidentiality/integrity violation of the intended per-service routing trust boundary, though it does not by itself expose private keys — keychain secret operations are gated separately through `KeychainServer`/`run_request` and require explicit keychain commands rather than passive message forwarding. [8](#0-7) 

### Likelihood Explanation
Exploitation only requires the ability to open a websocket connection to the local daemon and send a `register_service` command with an arbitrary service name — no ownership proof, token, or capability check is required beyond the existing TLS handshake. [9](#0-8)  Any co-resident local process capable of completing the daemon TLS handshake (e.g. any service-tier client, plotter, or GUI process) can trigger this, and the existing test suite exercises multi-registration behavior without asserting exclusivity of service ownership. [6](#0-5) 

### Recommendation
Bind service registration to caller identity: require that a websocket registering under a given service name present credentials/certificate identity matching that service (or maintain a server-side allowlist keyed by client certificate CN, similar to `validate_service()` for `start_service`), and reject or log registration attempts for service names not owned by the connecting identity.

### Proof of Concept
A local process with a valid daemon TLS client cert connects to the daemon websocket and sends:
```json
{"command": "register_service", "data": {"service": "chia_wallet"}, "destination": "daemon", "origin": "attacker"}
```
per `register_service()`'s handling. [4](#0-3)  From then on, any daemon message with `destination: "chia_wallet"` is forwarded to this websocket alongside the legitimate wallet RPC connection, per the daemon's destination-forwarding contract. [10](#0-9) 

---

**Uncertainty note**: I was unable to directly view the `handle_message`/message-forwarding implementation in `chia/daemon/server.py` (only the documented behavior in `.cursor/context/daemon.md` and `register_service`'s source) before the iteration limit, so the exact forwarding code path (line numbers) is not independently confirmed against source — it is based on the verified context doc. A Devin session with full file access would be needed to confirm the exact forwarding function and line ranges if precise citation is required.

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

**File:** .cursor/context/daemon.md (L17-19)
```markdown
- `WebSocketServer` is the daemon authority. It owns the TLS websocket listener,
  service registration table, daemon command dispatch, child-process table,
  plotting queue, keyring status notifications, and shutdown coordination.
```

**File:** .cursor/context/daemon.md (L50-57)
```markdown
- `destination != "daemon"` is pure service forwarding. The daemon does not
  interpret the command or payload if the destination is registered; it serializes
  the original message and sends it to all websockets registered under that
  destination.
- `register_service` adds the current websocket to `connections[service]`.
  Multiple registrations of the same websocket for the same service collapse
  through the set; one websocket may register for multiple services and
  `remove_connection()` must remove it from all of them.
```

**File:** .cursor/context/daemon.md (L103-105)
```markdown
- `start_service` accepts only names validated by `validate_service()`. A service
  is considered running if the daemon has a live tracked process or a registered
  websocket for that service, which supports services started outside the daemon.
```

**File:** chia/_tests/core/daemon/test_daemon_register.py (L38-66)
```python
@pytest.mark.anyio
async def test_multiple_register_different(get_daemon: WebSocketServer, bt: BlockTools) -> None:
    ws_server = get_daemon
    config = bt.config

    daemon_port = config["daemon_port"]

    # setup receive service to connect to the daemon
    async with aiohttp.ClientSession() as client:
        async with client.ws_connect(
            f"wss://127.0.0.1:{daemon_port}",
            autoclose=True,
            autoping=True,
            ssl=bt.get_daemon_ssl_context(),
            max_msg_size=100 * 1024 * 1024,
        ) as ws:
            test_service_names = ["service1", "service2", "service3"]

            for service_name in test_service_names:
                data = {"service": service_name}
                payload = create_payload("register_service", data, service_name, "daemon")
                await ws.send_str(payload)
                await ws.receive()

            assert sorted(ws_server.connections.keys()) == test_service_names

            for service_name in test_service_names:
                connections = ws_server.connections.get(service_name, set())
                assert len(connections) == 1
```

**File:** .cursor/context/rpc.md (L82-84)
```markdown
- Highest-risk edits: changing automatic `"success"` insertion, changing common route names, broadening `RpcClient.fetch()` failure behavior, altering daemon websocket command routing, or making RPC lifecycle assumptions about peer-server availability.
- Error compatibility is easy to break: HTTP includes traceback in failure responses, websocket failures do not, and `ResponseFailureError` preserves the full JSON body.
- The RPC boundary is semi-trusted local/admin surface protected by private TLS in normal service mode, not an untrusted P2P path. Do not move peer protocol rate-limiting or node-type authorization assumptions into this layer.
```

**File:** chia/daemon/keychain_server.py (L288-303)
```python
    async def run_request(self, request_dict: dict[str, Any], request_type: type[Any]) -> dict[str, Any]:
        keychain = self.get_keychain_for_request(request_dict)
        if keychain.is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        try:
            request = request_type.from_json_dict(request_dict)
        except Exception as e:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_MALFORMED_REQUEST,
                "error_details": {"message": str(e)},
            }

        try:
            return {"success": True, **request.run(keychain).to_json_dict()}
```
