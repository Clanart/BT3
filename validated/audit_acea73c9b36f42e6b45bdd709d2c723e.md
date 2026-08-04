## Analysis: Address-based authentication fallback in Tron `IntentGatewayV2`

The external report's core broken invariant is: **an address-equality check used as an identity/authorization gate silently gives the wrong answer in an edge case (default/fallback), letting an unverified party pass authentication.** The local analog is in the Tron port of `IntentGatewayV2`, which authenticates incoming cross-chain messages using an address match that has an unsafe default-to-self fallback, unlike the mainline EVM implementation.

### Title
Tron `IntentGatewayV2.instance()` fallback silently authenticates unregistered state machines against the contract's own address - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The mainline EVM `IntentGatewayV2` resolves cross-chain peers via `_instance()`, which reverts with `UnknownInstance` if no gateway has been explicitly registered for a state machine [1](#0-0) . The Tron variant instead exposes `instance()`, which returns `address(this)` when no gateway is registered for that state machine, rather than reverting [2](#0-1) . This value directly feeds `authenticate()`, the function that gates `RedeemEscrow`/`RefundEscrow` message processing [3](#0-2) .

### Finding Description
`authenticate()` checks `instance(request.source) != module`, where `module` is decoded from the 20-byte `request.from` field of the incoming ISMP `PostRequest` [3](#0-2) . For any `request.source` state machine that governance has never registered via a `NewDeployment` message (i.e., `_instances[keccak256(stateMachineId)] == address(0)`), `instance()` collapses to `address(this)` — the Tron gateway's own address — instead of rejecting the message. This is the same class of defect as `computePoolAddress()` in the external report: an address-derived identity check is trusted as a security boundary, but the fallback/edge-case path silently produces a value (`address(this)`) that was never meant to authorize third-party state machines.

This directly contradicts the explicit governance-allowlist model documented for the mainline gateway, where `_addDeployment`/`NewDeployment` is the only way a state machine peer becomes trusted, and any state machine not yet registered must be rejected (`UnknownInstance`) [4](#0-3) . The Tron port's `authenticate` re-implements the identical check pattern used elsewhere (`_authenticate` in `ExtrinsicIntents.sol`) [5](#0-4)  but backs it with the permissive `instance()` fallback instead of the strict-revert `_instance()`.

Because the protocol deliberately deploys `IntentGatewayV2` via `CREATE2` with identical salt/admin so the **same address exists on every chain** (explicitly documented intent: "the address depends on (impl address, salt, params, peer chain ids)... keeping the proxy address identical everywhere") [6](#0-5) , this fallback is not a theoretical edge case — it is the exact address the real gateway occupies on every chain, meaning the "default" identity coincides with a value that has real bridging significance rather than being a harmless zero/placeholder.

### Impact Explanation
`authenticate()` gates release of escrowed funds (`_withdraw` for `RedeemEscrow`/`RefundEscrow`) [7](#0-6) . A message-identity check that silently defaults to "trust myself" for any state machine that was never explicitly onboarded by governance removes the intended explicit-allowlist control (`NewDeployment`) as an actual security boundary for that class of source. This falls under "false proof/state acceptance" and "logic attacks" per the bounty scope: the gateway's binding of module identity to an explicitly governance-approved peer is not enforced uniformly — an un-onboarded state machine can still pass the identity check if the proven `from` field equals the destination's own address.

### Likelihood Explanation
Medium-low. Exploitation requires that a `PostRequest` proven to originate from an unregistered state machine carries `from == address(this)` (the destination contract's own 20-byte address). Because the destination address is fixed and public, and reproducing it via CREATE2 requires either (a) using the exact canonical `(deployer, salt, initcode-with-canonical-admin)` tuple — which only the real protocol admin can subsequently `initialize()` meaningfully — or (b) a ~2^160 salt-grinding search for an unrelated bytecode, the direct "attacker deploys a fully attacker-controlled fake gateway at that exact address" path is not practically achievable. The concretely provable defect is the **divergence from the safe pattern used elsewhere in the same codebase**: the Tron port silently treats "unregistered" as "trust self" instead of rejecting, which is a real regression in the module-identity invariant even though full end-to-end fund theft requires additional conditions (e.g., a not-yet-onboarded chain's genuine, canonically-deployed gateway sending a message before its `NewDeployment` registration completes) that this analysis could not fully verify were reachable by a fully unprivileged attacker within the available code.

### Recommendation
Replace `instance()`'s default-to-`address(this)` fallback with an explicit revert (mirroring `_instance()`'s `UnknownInstance` revert in the mainline EVM implementation), so `authenticate()` never treats an unregistered source state machine as implicitly trusted.

### Proof of Concept
Conceptual reproduction (not fully verified against a live unprivileged-attacker path):
1. Governance has not yet called `NewDeployment` for state machine `X` on the Tron `IntentGatewayV2` (`_instances[keccak256(X)] == 0`).
2. A `PostRequest` is proven (via a valid ISMP state proof) with `source = X` and `from = <20 bytes equal to the Tron gateway's own address>`, body encoding `RedeemEscrow`/`RefundEscrow`.
3. `onAccept` → `authenticate(request)` computes `instance(X)`, which returns `address(this)` (fallback) since `X` is unregistered [8](#0-7) .
4. `module == address(this)` so the check passes, and `_withdraw` releases escrowed funds, even though `X` was never approved as a peer via governance's `NewDeployment` flow.

Note: step 2 requires an actual contract at address `address(this)` (Tron gateway's canonical address) to exist and dispatch such a message on chain `X`, which in practice implies the genuine, canonically-deployed gateway rather than an attacker-fabricated contract — this limits the practical severity to a premature-trust/onboarding-ordering issue rather than a clean unprivileged fund-theft primitive, and this could not be fully confirmed as attacker-reachable with the code available.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L514-524)
```text
    /**
     * @dev Registers a new IntentGateway deployment for a remote state machine.
     * Called when Hyperbridge governance adds support for a new chain. The gateway
     * address is stored in `_instances` keyed by the hash of the state machine ID.
     *
     * @param body The deployment info containing the state machine ID and gateway address.
     */
    function _addDeployment(Deployment memory body) internal {
        _instances[keccak256(body.chain)] = body.gateway;
        emit DeploymentAdded({chain: string(body.chain), gateway: body.gateway});
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L281-294)
```text
    function instance(bytes calldata stateMachineId) public view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        return gateway == address(0) ? address(this) : gateway;
    }

    /**
     * @dev Checks that the request originates from a known instance of the IntentGateway.
     */
    function authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        // IntentGateway only accepts incoming assets from itself or known instances
        if (instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/script/DeployIntentGateway.s.sol (L20-26)
```text
    function deploy() internal override {
        // Deploy implementation and proxy via CREATE2 with the same salt. The proxy is initialized
        // atomically through its init data. The cross-chain peer registry is passed in by chain id
        // only — `initialize` binds each to `address(this)` — so no peer address is embedded in the
        // init data. The address depends on (impl address, salt, params, peer chain ids), all of
        // which are identical across chains, keeping the proxy address identical everywhere.
        address priceOracle = address(0);
```
