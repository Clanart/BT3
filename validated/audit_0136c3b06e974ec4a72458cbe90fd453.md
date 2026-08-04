## Title
`IntentGatewayV2.instance()` (Tron variant) falls back to `address(this)` for unregistered source chains, allowing unauthenticated cross-chain messages to be accepted - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
This is a local analog of the DYAD `setUnboundedKerosineVault` bug class: a configuration value that is never set for a given entity (there, the unbounded vault pointer; here, a peer-gateway registration for a given source chain) is not guarded with a revert, but instead silently falls back to a default that gets treated as legitimate. In DYAD the unset value caused an unwanted revert (DoS); in this Tron `IntentGatewayV2`, the unset value (`_instances[keccak256(stateMachineId)] == address(0)`) causes the code to *substitute a default that passes an authorization check*, which is a strictly worse outcome — false-state acceptance instead of a safe revert.

### Finding Description
The Tron build of `IntentGatewayV2` implements peer-authentication like this: [1](#0-0) 

```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}

function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    // IntentGateway only accepts incoming assets from itself or known instances
    if (instance(request.source) != module) revert Unauthorized();
}
```

Compare this with the canonical (non-Tron) `IntentGatewayV2.sol`, whose `_instance` helper is documented to explicitly `revert UnknownInstance` for any state machine that was never registered via `_addDeployment`/governance: [2](#0-1) 

In the Tron variant, `instance()` never reverts for an unregistered chain — it returns `address(this)`, i.e. the local gateway's own address. Because `IntentGatewayV2`'s address is deterministic (CREATE2, same salt/bytecode across chains — the same design principle used elsewhere in this repo, e.g. `DeployScript` comments in `evm/script/DeploySolverAccount.s.sol` and `evm/tron/contracts/apps/IntentGatewayV2.sol` peer registration relying on `address(this)`), this address is public and predictable.

`authenticate()` is used to gate `onAccept` for incoming ISMP requests (`RedeemEscrow`, `NewDeployment`, `UpdateParams`, `SweepDust`, `RefundEscrow`). The check `instance(request.source) != module` becomes `address(this) != module`. Any legitimately-delivered ISMP `PostRequest` (i.e., proven via a real, consensus-verified state proof from *any* Hyperbridge-supported chain that the operator simply never got around to registering as a peer in `_instances`) whose `from` field encodes `address(this)` will pass authentication as if it came from a real, registered `IntentGatewayV2` peer instance.

### Impact Explanation
This breaks the module/app-identity binding invariant explicitly called out in the pivots ("Request, response, and timeout paths must bind chain id, module/app identity, commitment uniqueness ... on both Substrate and EVM"). An unregistered/misconfigured peer chain silently degrades into an accepted one instead of causing a safe revert, enabling unauthorized execution of privileged `onAccept` actions (e.g., `RedeemEscrow`/`RefundEscrow`, which move escrowed funds, or `UpdateParams`, which changes protocol fee/oracle settings) attributed to a source chain that was never actually vetted/whitelisted as a peer. This is a false-acceptance / unauthorized-execution class bug, matching "unauthorized transaction or execution" and "false proof/state acceptance" in the required impact gate.

### Likelihood Explanation
The trigger condition is purely operational: any state machine supported by the local Hyperbridge host but not yet registered in `_instances` for this specific gateway deployment (a very plausible, unprivileged-attacker-exploitable gap — deployments are rolled out per-chain over time, exactly like the DYAD post-deployment `setUnboundedKerosineVault` gap). No malicious relayer, prover, or governance actor is required — the attacker only needs to deploy anything on an unregistered-but-consensus-verified chain and dispatch a real ISMP `PostRequest` with `from = address(this)` (a public, deterministic value) to the target gateway.

### Recommendation
Mirror the canonical `IntentGatewayV2.sol` behavior: make `instance()` revert (e.g. `UnknownInstance`) when `_instances[keccak256(stateMachineId)] == address(0)`, rather than defaulting to `address(this)`. Authentication should only ever succeed for state machines explicitly registered via `_addDeployment`/governance.

### Proof of Concept
1. Deploy `IntentGatewayV2` (Tron variant) on chain `A`; do not register a peer instance for chain `B` (a real state machine Hyperbridge's host on `A` already tracks/verifies consensus for).
2. From chain `B`, deploy any contract and dispatch a real ISMP `PostRequest` to `A`'s `IntentGatewayV2` with `request.source = B`, `request.to = address(intentGatewayV2_on_A)`, and `request.from = abi.encodePacked(address(intentGatewayV2_on_A))` (the deterministic CREATE2 address, publicly known).
3. Have this request delivered through the real ISMP pipeline (valid state proof for chain `B`, since `B` is a legitimately supported/verified state machine — no forged consensus needed).
4. On `A`, `onAccept` calls `authenticate(request)` → `instance(B)` returns `address(this)` (fallback, since `B` was never registered) → `module == address(this)` → check passes, `Unauthorized` is never raised.
5. The embedded `RequestKind` payload (e.g. `RedeemEscrow`/`RefundEscrow`/`UpdateParams`) executes as if it came from a genuine, whitelisted `IntentGatewayV2` peer on `B`. [1](#0-0)

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L278-294)
```text
    /**
     * @dev Fetch the IntentGateway contract instance for a chain.
     */
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

**File:** evm/src/apps/IntentGatewayV2.sol (L94-115)
```text
    /**
     * @dev One-time init (the `initializer` modifier caps it to a single call). Registers the
     * initial cross-chain peers, each bound to `address(this)`; `_instance` reverts with
     * `UnknownInstance` for any chain not registered here or later via `onAccept` governance.
     *
     * @param p The initial gateway configuration parameters.
     * @param peerChains State-machine ids of the cross-chain peers to register. Each is bound to
     * this gateway's own address, identical across chains under deterministic CREATE2, so no peer
     * address is carried in the proxy's init data.
     */
    function initialize(Params memory p, bytes[] memory peerChains) public initializer {
        uint256 peersLength = peerChains.length;
        for (uint256 i = 0; i < peersLength; i++) {
            Deployment memory deployment = Deployment({
                chain: peerChains[i],
                gateway: address(this)
            });
            _addDeployment(deployment);
        }
        _validateParams(p);
        _params = p;
    }
```
