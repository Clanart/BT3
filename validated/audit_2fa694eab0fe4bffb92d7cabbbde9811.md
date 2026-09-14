### Title
Chia daemon/keychain RPC trusts client-supplied `kc_user`/`kc_service` namespace selectors without verifying the caller is authorized for that keychain identity - (File: chia/daemon/keychain_server.py)

### Summary
The Netmaker advisory (GHSA-hmqr-wjmj-376c) describes an `Authorise` middleware that validates *that a token is a valid host token* but never checks whether the specific host identified in the request is the one entitled to the targeted resource — a valid credential is treated as authorization for any arbitrary object ID supplied in the request. The Chia daemon's keychain RPC boundary has the analogous structural flaw: any client that has established a daemon websocket connection (which only proves it holds a valid local daemon mTLS client certificate) can supply arbitrary `kc_user`/`kc_service` values in the JSON `data` payload of any keychain command, and `KeychainServer.get_keychain_for_request()` will transparently open or create a `Keychain` for that namespace and perform the requested operation — including returning private keys and mnemonic entropy — with no check that the connecting client is entitled to that particular user/service namespace.

### Finding Description
`KeychainServer.get_keychain_for_request()` derives which keychain namespace to operate on purely from client-supplied strings in the request body: [1](#0-0) 

Every keychain-server operation (`add_key`, `delete_all_keys`, `delete_key_by_fingerprint`, `get_all_private_keys`, `get_first_private_key`, `get_key_for_fingerprint`, and the `run_request`-based `get_key`/`get_keys`/`set_label`/`delete_label`) calls `get_keychain_for_request(request)` and then operates on whatever keychain instance it returns, e.g.: [2](#0-1) [3](#0-2) 

These handlers are reached from `WebSocketServer.handle_message()`, which dispatches any incoming daemon command whose name is in `keychain_commands` straight to `KeychainServer.handle_command()` using the caller-supplied `data` dict, without any further per-namespace authorization step: [4](#0-3) 

The daemon's *transport* authenticates the connecting process via mutual TLS (client certificate under the same `CHIA_ROOT`) — this proves the caller is "a local, valid client" (analogous to Netmaker's "valid host token"), but it says nothing about which keychain namespace ("host"/"resource") that caller is entitled to touch. `KeychainProxy.format_request()` shows that `kc_user`/`kc_service` are ordinary values placed into the request body by the *client*, not derived from any server-side identity binding: [5](#0-4) 

Because `get_keychain_for_request()` will happily construct a brand-new `Keychain(user=user, service=service)` for any `kc_user`/`kc_service` pair it hasn't seen before and cache it in `_alt_keychains`, any daemon-connected client (e.g. any local service or GUI process that can complete the mTLS handshake) can address and read/mutate a different logical keychain namespace than its own simply by naming it in the request — mirroring Netmaker's `hostAllowed=true` bypass, where "possessing a valid credential" is conflated with "being authorized for this specific object."

### Impact Explanation
If a locally running but otherwise unrelated or lower-privileged daemon client (any process capable of connecting to the daemon's TLS websocket with the shared daemon certs) can specify an arbitrary `kc_user`/`kc_service`, it can call `get_all_private_keys`, `get_first_private_key`, `get_key_for_fingerprint`, or `get_key` (with `include_secrets=True`) against a keychain namespace it does not own, exfiltrating private keys/mnemonic entropy belonging to another logical keychain user configured on the same host. It can also call `delete_all_keys` / `delete_key_by_fingerprint` against that other namespace, destroying wallet key material it has no legitimate claim to. This is a concrete unauthorized-access/data-exfiltration and destructive-action path against key material once local daemon connectivity is available, matching the "insufficient authorization / object-level bypass" bug class in the source report and reaching Chia's key-derivation/custody surface, which is in scope for this review.

### Likelihood Explanation
Exploitation requires the attacker to already be capable of establishing a websocket connection to the local daemon (i.e., possess valid local daemon TLS client certs, which are shared per-`CHIA_ROOT` rather than scoped per keychain-namespace). Given that capability, exploitation is trivial: no path traversal, no cryptographic bypass, and no race condition are required — only setting two string fields in the JSON RPC payload. The severity is bounded by the fact that this is a local/admin RPC surface (not remote network-exposed by default), which is consistent with how `rpc.md`/`daemon.md` describe the boundary as "semi-trusted local/admin," but within that trust boundary there is no additional authorization gate at all for keychain-namespace selection, so any process that can reach the daemon socket (including a compromised or malicious co-located service, or a user context different from the one that owns a given keychain namespace) can pivot across keychain namespaces.

### Recommendation
Bind the keychain namespace to the authenticated connection/session rather than to attacker-controlled request fields: derive `kc_user`/`kc_service` (or an equivalent namespace identity) from the daemon's own knowledge of which OS user/service registered/authenticated the connection (e.g., from the registered service name or a server-side session identity established at TLS/registration time), and reject or ignore client-supplied `kc_user`/`kc_service` overrides for security-sensitive operations. At minimum, add an explicit authorization check in `KeychainServer.get_keychain_for_request()` (or in `handle_command()`) that validates the requesting connection is entitled to the requested namespace before returning/creating a `Keychain` instance for it, especially for `include_secrets=True` reads and delete operations.

### Proof of Concept
1. Start the chia daemon normally; obtain valid daemon client TLS certs for the local machine (as any locally co-installed service/tooling would have).
2. Connect a websocket client to the daemon and send a `register_service` message, then send a keychain command such as:
   ```json
   {"command": "get_all_private_keys", "data": {"kc_user": "victim-user", "kc_service": "victim-service"}, ...}
   ```
   using `chia.util.ws_message.create_payload("get_all_private_keys", {"kc_user": "victim-user", "kc_service": "victim-service"}, "attacker_service", "daemon")`, mirroring the request construction in [5](#0-4) .
3. `KeychainServer.handle_command()` routes this to `get_all_private_keys()`, which calls `get_keychain_for_request(request)` [2](#0-1) ,
   which opens/creates a `Keychain(user="victim-user", service="victim-service")` per [6](#0-5) 
   and returns that namespace's private keys/entropy in the response — even though the connecting client authenticated only as "attacker_service" and never proved ownership of the `victim-user`/`victim-service` keychain namespace.

### Citations

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

**File:** chia/daemon/server.py (L424-438)
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
        elif command == "ping":
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
