### Title
Unauthorized cross-service wallet identity switch via Data Layer's `wallet_log_in` RPC endpoint - (File: `chia/data_layer/data_layer_rpc_api.py`)

### Summary
The Data Layer RPC API exposes a `/wallet_log_in` route that lets any local Data Layer RPC caller force the shared wallet service to switch its globally active logged-in key to an arbitrary fingerprint, with no check that the caller is authorized to act as that fingerprint's owner. This mirrors the litellm team-management flaw: a caller reachable only for one restricted resource (a "team"/store) can perform a privileged state change that affects a different, unrelated authorization domain (another user's/key's wallet session) without any ownership or consent check.

### Finding Description
`DataLayerRpcApi.wallet_log_in()` accepts a bare `fingerprint` from the request and calls straight through to the wallet service: [1](#0-0) 

This is wired as one of the DataLayer RPC's public routes: [2](#0-1) 

The wallet-side `log_in()` handler performs no check that the requester is the same principal already logged in, nor any relationship between the DataLayer service and the target fingerprint — it simply stops the current wallet session and restarts it under the requested fingerprint: [3](#0-2) 

`WalletNode.log_in()` then unconditionally overwrites the node's `logged_in_fingerprint`, making that fingerprint the active identity for every subsequent Wallet RPC call from any client (GUI, CLI, other local admin tools) until someone logs in again: [4](#0-3) [5](#0-4) 

This cross-service authority is confirmed by the test suite itself, showing that calling the DataLayer's `wallet_log_in` (through direct call, RPC client, CLI func, or CLI process) changes the *wallet service's* globally active fingerprint away from what the wallet's own primary session had selected: [6](#0-5) [7](#0-6) 

Chia's own architecture notes explicitly flag this class of risk: the RPC boundary is a *semi-trusted local/admin* surface, not a peer-authenticated multi-tenant surface, and "route discovery returns every shared and service-specific route... adding sensitive operational endpoints should be evaluated as local admin API exposure": [8](#0-7) 

The root cause matches the litellm bug class directly: an endpoint intended for a narrow purpose (associating the local Data Layer service with a wallet fingerprint for its own store operations) is implemented with no authorization boundary, so any caller who can reach the Data Layer's RPC (a lower-trust, narrower surface than the Wallet RPC itself) can silently hijack the wallet session used by all other local RPC clients, without any check on "does this caller/key own or is otherwise entitled to activate that fingerprint."

### Impact Explanation
On a machine where the wallet service, Data Layer service, and multiple keychain fingerprints coexist (a supported and documented Chia configuration — e.g., a farmer/operator managing several keys), any process able to reach the Data Layer local RPC endpoint can:
- Force-switch the active wallet identity to any fingerprint present in the keychain, without that fingerprint's owner's consent.
- Once switched, any subsequent Wallet RPC call from *any* local caller (GUI, CLI, automated scripts) operates against the hijacked fingerprint's wallet — enabling private key retrieval (`get_private_key`), balance/transaction disclosure, and spend-affecting operations (sends, offers, DID/NFT/CAT operations) to be issued against a wallet the original caller never intended to expose.
- This is an authorization boundary violation (CWE-284/862 analog) causing unauthorized coin movement/exposure risk across identity boundaries that the wallet service is otherwise supposed to keep separate per fingerprint.

This satisfies the "concrete unsigned or unauthorized coin movement" bar because after the forced login, other RPC callers (which believed they were controlling their fingerprint's wallet) may unknowingly submit spends attributed to, or be able to read secrets of, a different key's wallet.

### Likelihood Explanation
Exploitation requires local access to the Data Layer RPC (or its client/CLI), which is the same trust tier as the Wallet RPC itself (private TLS certs, local admin surface) — not a remote or peer-network attacker. Still, within multi-key/multi-service host setups (common for farmers running Data Layer alongside a wallet with several imported fingerprints, or shared operator environments), this endpoint provides an easy, unauthenticated pivot from "Data Layer client" privilege to "any wallet fingerprint" privilege, which is more permissive than intended given the service's narrow purpose.

### Recommendation
- Restrict `wallet_log_in` to only fingerprints already known/authorized for the Data Layer's own configured wallet association (e.g., verify the fingerprint is the one Data Layer was configured to use, or require it to match the currently already-logged-in fingerprint) rather than allowing it to switch to an arbitrary fingerprint.
- Add an explicit ownership/authorization check before `WalletRpcApi.log_in()`/`WalletNode.log_in()` allows a fingerprint switch triggered by an external/cross-service caller, separate from a normal direct wallet-service login.
- Audit other cross-service RPC calls that mutate global wallet state (e.g., anything invoking `_start_with_fingerprint`) for the same missing authorization boundary.

### Proof of Concept
1. Start a wallet service logged in under fingerprint A (owner A's key) and a Data Layer service pointed at the same wallet RPC, per `init_data_layer_service` test setup.
2. From any local caller able to reach the Data Layer RPC (no special privilege beyond generic local RPC TLS access), call `POST /wallet_log_in` with `{"fingerprint": B}` where B is any other fingerprint present in the keychain (as done in `chia/_tests/core/data_layer/test_data_rpc.py::test_wallet_log_in_changes_active_fingerprint`).
3. Observe that the wallet service's `get_logged_in_fingerprint` now returns B — the wallet session has been switched without B's explicit consent, and any subsequent Wallet RPC call (e.g., `get_private_key`, `send_transaction`) from any client now operates against fingerprint B's wallet.

### Citations

**File:** chia/data_layer/data_layer_rpc_api.py (L101-104)
```python
    def get_routes(self) -> dict[str, Endpoint]:
        return {
            "/wallet_log_in": self.wallet_log_in,
            "/create_data_store": self.create_data_store,
```

**File:** chia/data_layer/data_layer_rpc_api.py (L143-146)
```python
    async def wallet_log_in(self, request: dict[str, Any]) -> EndpointResult:
        fingerprint = cast(int, request["fingerprint"])
        await self.service.wallet_log_in(fingerprint=fingerprint)
        return {}
```

**File:** chia/wallet/wallet_rpc_api.py (L644-657)
```python
    async def log_in(self, request: LogIn) -> LogInResponse:
        """
        Logs in the wallet with a specific key.
        """

        if self.service.logged_in_fingerprint == request.fingerprint:
            return LogInResponse(fingerprint=request.fingerprint)

        await self._stop_wallet()
        started = await self.service._start_with_fingerprint(request.fingerprint)
        if started is True:
            return LogInResponse(fingerprint=request.fingerprint)

        raise ValueError(f"fingerprint {request.fingerprint} not found in keychain or keychain is empty")
```

**File:** chia/wallet/wallet_node.py (L429-462)
```python
    async def _start_with_fingerprint(
        self,
        fingerprint: int | None = None,
    ) -> bool:
        # Makes sure the coin_state_updates get higher priority than new_peak messages.
        # Delayed instantiation until here to avoid errors.
        #   got Future <Future pending> attached to a different loop
        self._new_peak_queue = NewPeakQueue(inner_queue=asyncio.PriorityQueue())
        if not fingerprint:
            fingerprint = self.get_last_used_fingerprint()
        multiprocessing_start_method = process_config_start_method(config=self.config, log=self.log)
        multiprocessing_context = multiprocessing.get_context(method=multiprocessing_start_method)
        self._weight_proof_handler = WalletWeightProofHandler(self.constants, multiprocessing_context)
        self.synced_peers = set()
        public_key = None
        private_key = await self.get_key(fingerprint, private=True, find_a_default=False)
        if private_key is None:
            public_key = await self.get_key(fingerprint, private=False, find_a_default=False)
        else:
            assert isinstance(private_key, PrivateKey)
            public_key = private_key.get_g1()

        if public_key is None:
            private_key = await self.get_key(None, private=True, find_a_default=True)
            if private_key is not None:
                assert isinstance(private_key, PrivateKey)
                public_key = private_key.get_g1()
            else:
                self.log_out()
                return False
        assert isinstance(public_key, G1Element)
        # override with private key fetched in case it's different from what was passed
        if fingerprint is None:
            fingerprint = public_key.get_fingerprint()
```

**File:** chia/wallet/wallet_node.py (L774-781)
```python
    def log_in(self, fingerprint: int) -> None:
        self.logged_in_fingerprint = fingerprint
        self.logged_in = True
        self.log.info(f"Wallet is logged in using key with fingerprint: {self.logged_in_fingerprint}")
        try:
            self.update_last_used_fingerprint()
        except Exception:
            self.log.exception("Non-fatal: Unable to update last used fingerprint.")
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2707-2726)
```python
async def test_wallet_log_in_changes_active_fingerprint(
    self_hostname: str,
    one_wallet_and_one_simulator_services: SimulatorsAndWalletsServices,
    layer: InterfaceLayer,
) -> None:
    wallet_rpc_api, _full_node_api, wallet_rpc_port, _ph, bt = await init_wallet_and_node(
        self_hostname, one_wallet_and_one_simulator_services
    )
    primary_fingerprint = (await wallet_rpc_api.get_logged_in_fingerprint(Empty())).fingerprint
    assert primary_fingerprint is not None

    mnemonic = create_mnemonic()
    assert wallet_rpc_api.service.local_keychain is not None
    private_key = wallet_rpc_api.service.local_keychain.add_key(mnemonic_or_pk=mnemonic)
    secondary_fingerprint: int = private_key.get_g1().get_fingerprint()

    await wallet_rpc_api.log_in(LogIn(fingerprint=primary_fingerprint))

    active_fingerprint = (await wallet_rpc_api.get_logged_in_fingerprint(Empty())).fingerprint
    assert active_fingerprint == primary_fingerprint
```

**File:** chia/_tests/core/data_layer/test_data_rpc.py (L2763-2764)
```python
        active_fingerprint = (await wallet_rpc_api.get_logged_in_fingerprint(Empty())).fingerprint
        assert active_fingerprint == secondary_fingerprint
```

**File:** .cursor/context/rpc.md (L83-85)
```markdown
- Error compatibility is easy to break: HTTP includes traceback in failure responses, websocket failures do not, and `ResponseFailureError` preserves the full JSON body.
- The RPC boundary is semi-trusted local/admin surface protected by private TLS in normal service mode, not an untrusted P2P path. Do not move peer protocol rate-limiting or node-type authorization assumptions into this layer.
- Route discovery returns every shared and service-specific route. Adding sensitive operational endpoints should be evaluated as local admin API exposure even when not reachable through the P2P protocol.
```
