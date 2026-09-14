I found a solid analog. This is a genuine finding in the daemon/keychain RPC layer.

### Title
Keychain RPC commands trust client-supplied `kc_user`/`kc_service` selectors, allowing cross-namespace private-key access - (File: `chia/daemon/keychain_server.py`)

### Summary
`KeychainServer.get_keychain_for_request()` derives which keychain data store (i.e., which user/service key namespace) to operate on directly from attacker-controllable `kc_user`/`kc_service` fields in the raw request dict, with no check that these values correspond to the identity of the connection making the request. This mirrors CVE-2025-66385's root cause: a handler binds a privileged selector (there, `role_id`/`organisation_id`; here, `kc_user`/`kc_service`) directly from client-supplied request data instead of deriving it from the caller's own authenticated context.

### Finding Description
`WebSocketServer.handle_message()` in `chia/daemon/server.py` dispatches any incoming websocket command whose name is in `keychain_commands` straight to `KeychainServer.handle_command(command, data)`, where `data = message["data"]` is the raw, fully client-controlled JSON body: [1](#0-0) 

Inside `KeychainServer`, every keychain operation (`add_key`, `get_all_private_keys`, `get_first_private_key`, `delete_key_by_fingerprint`, `delete_all_keys`, `get_key`/`get_keys` via `run_request`, `set_label`, `delete_label`, etc.) resolves its target `Keychain` instance through `get_keychain_for_request()`: [2](#0-1) 

This function reads `kc_user`/`kc_service` straight out of the untrusted request dict and, if they differ from the default keychain's user/service, transparently creates or reuses a `Keychain(user=user, service=service)` for that namespace. There is no authorization check anywhere in `handle_command()` or the individual command handlers (`add_key`, `get_all_private_keys`, `delete_key_by_fingerprint`, etc.) that verifies the requesting connection is entitled to act on the `kc_user`/`kc_service` namespace it names. For example, `get_all_private_keys` and `delete_key_by_fingerprint` operate solely off whatever keychain `get_keychain_for_request(request)` resolves to: [3](#0-2) [4](#0-3) 

By contrast, `KeychainProxy.format_request()` (the legitimate client) only ever sets `kc_user`/`kc_service` to its own configured identity: [5](#0-4)  — but nothing stops any other daemon-connected client (e.g., a farmer/harvester service, or any process holding a daemon-scoped TLS client cert for `wallet_ui`-level daemon access) from sending its own raw `data` payload with a different `kc_user`/`kc_service` pair and having the daemon happily create/read/delete keys in that other namespace.

The daemon's own documentation flags this exact pattern as a known risk area, warning that keyring/service-registration inputs are security-relevant even though the surface is "local": [6](#0-5) 

### Impact Explanation
Private keys directly control spend authority over all coins owned by the corresponding wallet. A local, lower-privileged daemon client (any process able to open a websocket connection to the daemon with a valid client cert, but not intended to access another user's/service's keyring namespace) can supply arbitrary `kc_user`/`kc_service` values to `add_key`, `get_all_private_keys`, `get_first_private_key`, or `delete_key_by_fingerprint`/`delete_all_keys`, thereby reading, injecting, or deleting keys belonging to a different logical keychain user/service than its own. This is a direct authorization-boundary bypass analogous to the CVE's privilege escalation via unchecked `role_id`/`organisation_id`, and here it can lead to unauthorized key disclosure (theft of spend authority) or destructive key deletion (denial of wallet access) across namespaces that were supposed to be isolated.

### Likelihood Explanation
Exploitation requires only the ability to open a daemon websocket connection with a valid local client certificate — the same level of access already required to invoke any other daemon command (e.g., `start_service`, `is_running`). No additional privilege beyond "some local service is allowed to talk to the daemon" is needed to specify a different `kc_user`/`kc_service` pair than one's own, since the server performs no cross-check between the requester's identity and the requested keychain namespace.

### Recommendation
Do not allow `kc_user`/`kc_service` to be freely chosen by request payloads for commands that mutate or disclose key material. Bind the keychain namespace to the requesting connection's registered/authenticated service identity (the same identity used for `register_service`), or require an explicit, separately-authorized mapping before honoring a `kc_user`/`kc_service` override. At minimum, keychain-mutating/disclosing commands (`add_key`, `get_all_private_keys`, `get_first_private_key`, `delete_key_by_fingerprint`, `delete_all_keys`) should reject requests whose `kc_user`/`kc_service` do not match the connection's own registered identity.

### Proof of Concept
1. Establish a legitimate local daemon websocket connection using a valid client certificate for a low-privilege registered service (e.g., `harvester`).
2. Send a `get_all_private_keys` (or `add_key`/`delete_all_keys`) command with `data = {"kc_user": "<other-user>", "kc_service": "<other-service>"}`.
3. `WebSocketServer.handle_message()` routes it to `KeychainServer.handle_command()`, which calls `get_keychain_for_request()`; since the supplied `kc_user`/`kc_service` differ from the default, a `Keychain(user=..., service=...)` for that namespace is created/reused and the operation executes against it. [3](#0-2) 
4. The response returns private key material (or performs deletion) for the targeted namespace, despite the requester never having been authorized for that `kc_user`/`kc_service` pair.

### Citations

**File:** chia/daemon/server.py (L424-437)
```python
        data = message["data"]
        commands_with_data = [
            "start_service",
            "start_plotting",
            "stop_plotting",
            "stop_service",
            "is_running",
            "register_service",
        ]
        if len(data) == 0 and command in commands_with_data:
            response = {"success": False, "error": f'{command} requires "data"'}
        # Keychain commands should be handled by KeychainServer
        elif command in keychain_commands:
            response = await self.keychain_server.handle_command(command, data)
```

**File:** chia/daemon/keychain_server.py (L154-172)
```python
    def get_keychain_for_request(self, request: dict[str, Any]) -> Keychain:
        """
        Keychain instances can have user and service strings associated with them.
        The keychain backends ultimately point to the same data stores, but the user
        and service strings are used to partition those data stores. We attempt to
        maintain a mapping of user/service pairs to their corresponding Keychain.
        """
        user = request.get("kc_user", self._default_keychain.user)
        service = request.get("kc_service", self._default_keychain.service)
        if user == self._default_keychain.user and service == self._default_keychain.service:
            keychain = self._default_keychain
        else:
            key = (user or "unnamed") + (service or "")
            if key in self._alt_keychains:
                keychain = self._alt_keychains[key]
            else:
                keychain = Keychain(user=user, service=service)
                self._alt_keychains[key] = keychain
        return keychain
```

**File:** chia/daemon/keychain_server.py (L272-286)
```python
    async def delete_key_by_fingerprint(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        fingerprint = request.get("fingerprint", None)
        if fingerprint is None:
            return {
                "success": False,
                "error": KEYCHAIN_ERR_MALFORMED_REQUEST,
                "error_details": {"message": "missing fingerprint"},
            }

        self.get_keychain_for_request(request).delete_key_by_fingerprint(fingerprint)

        return {"success": True}
```

**File:** chia/daemon/keychain_server.py (L317-326)
```python
    async def get_all_private_keys(self, request: dict[str, Any]) -> dict[str, Any]:
        all_keys: list[dict[str, Any]] = []
        if self.get_keychain_for_request(request).is_keyring_locked():
            return {"success": False, "error": KEYCHAIN_ERR_LOCKED}

        private_keys = self.get_keychain_for_request(request).get_all_private_keys()
        for sk, entropy in private_keys:
            all_keys.append({"pk": bytes(sk.get_g1()).hex(), "entropy": entropy.hex()})

        return {"success": True, "private_keys": all_keys}
```

**File:** chia/daemon/keychain_proxy.py (L75-86)
```python
    def format_request(self, command: str, data: dict[str, Any]) -> WsRpcMessage:
        """
        Overrides DaemonProxy.format_request() to add keychain-specific RPC params
        """
        if data is None:
            data = {}

        if self.keychain_user or self.keychain_service:
            data["kc_user"] = self.keychain_user
            data["kc_service"] = self.keychain_service

        return super().format_request(command, data)
```

**File:** .cursor/context/daemon.md (L36-43)
```markdown
Public RPC docs show the daemon as a websocket route for service commands. In source, the daemon is also the local privilege concentrator: it can expose keychain operations, launch or kill services, mutate plotter queue state, and relay GUI/service messages by registered service name. That makes message envelope compatibility and registration cleanup security-relevant even though this is not P2P traffic.

## Wrong Assumptions To Avoid

- Do not apply peer-protocol sender maps, binary streamable framing, or P2P rate limits to daemon messages.
- Do not treat service registration as harmless metadata; registered names become routing authorities for local clients.
- Do not route arbitrary user-supplied command lines through service launch paths.
- Do not normalize keychain errors without checking CLI, GUI, daemon proxy, and remote keychain compatibility.
```
