### Title
Unregistered source chains default to `address(this)` in `IntentGatewayV2.instance()`, allowing forged cross-chain messages to bypass peer authentication - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Tron deployment of `IntentGatewayV2` resolves peer gateway addresses with a function that silently defaults to `address(this)` for any unregistered state machine, instead of reverting like the canonical EVM implementation does. This is the same "ambiguous default masks the real broken invariant" bug class as the reported fallback/receive issue: a code path that is supposed to gate on a specific, unambiguous condition instead falls back to a default value that an attacker can trivially satisfy, silently disabling the intended access control.

### Finding Description
`authenticate()` is the sole gate that determines whether an incoming ISMP `PostRequest` handled by `onAccept` (e.g. `RedeemEscrow`, `RefundEscrow`) is trusted: [1](#0-0) 

```
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

`instance()` should revert when `request.source` is not a registered peer deployment, exactly as the canonical (non-Tron) implementation does: [2](#0-1) 

```
function _instance(bytes calldata stateMachineId) internal view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    if (gateway == address(0)) revert UnknownInstance();
    return gateway;
}
```

Instead, the Tron variant treats "no registered instance for this `stateMachineId`" as equivalent to "the instance is `address(this)`". Combined with `authenticate`, this means: for **any** `request.source` state machine that was never registered via `NewDeployment`/`_addDeployment`, a message is accepted as long as `request.from == address(this)` (the gateway's own address encoded as the sender). Since the concrete IntentGateway contract address is deterministic and public (CREATE2), an attacker does not need to guess anything — they simply need the Hyperbridge host to deliver a `PostRequest` whose `source` is an unregistered/arbitrary state machine id and whose `from` field is set to `address(this)`.

This mirrors the original bug class precisely: the "fallback" path (unregistered instance) is silently handled by a default (`address(this)`) that the code's own logic elsewhere treats as a legitimate, trusted value, rather than being rejected outright — exactly like the ModuleManager `receive()`/`fallback()` ambiguity defaulting `bytes4(0)` to an unintended handler.

### Impact Explanation
`onAccept` uses `authenticate()` to gate privileged, fund-moving request kinds such as `RedeemEscrow` (release escrow to the filler/solver) and `RefundEscrow` (refund escrow to the user) — i.e. it is the sole check standing between an arbitrary incoming ISMP message and cross-chain fund custody movement in the intents escrow. If `instance()` defaults an unregistered source to `address(this)`, `authenticate` can be satisfied by anyone able to get a `PostRequest` routed to this module from a chain id that was never configured as a peer, as long as `request.from` is padded/encoded to equal the gateway's own address. This directly threatens "stealing or loss of funds" and "false proof/state acceptance" per the bounty's impact gate — escrowed order funds could be redeemed or refunded to an attacker-controlled beneficiary without a legitimate fill/cancellation ever occurring on the counterpart chain.

### Likelihood Explanation
Exploitability depends on whether the ISMP host will actually route a request whose `source` is an arbitrary/unregistered state machine id to this module's `onAccept` (the host itself must accept and forward it, which requires a valid consensus/state proof for that state machine per the ISMP host handler). This condition is a genuine gap in this module's own authorization logic (not a "malicious relayer/prover" assumption) — the bug is that `instance()` should never treat "unknown" as "self", regardless of how the request was routed. Given that the canonical EVM contract explicitly hardens this exact case with `UnknownInstance()`, the Tron file's divergence looks like a regression/incomplete port rather than intentional design, which raises confidence this is a real, locally provable defect in a live contract variant.

### Recommendation
Change `instance()` (or remove it and use only an internal helper) to revert with `UnknownInstance` (or equivalent) when `_instances[keccak256(stateMachineId)] == address(0)`, matching the safe pattern already implemented in `evm/src/apps/intentsv2/IntentsBase.sol::_instance`. Never allow "instance not found" to resolve to `address(this)` or any other address that `authenticate` treats as trusted.

### Proof of Concept
1. Deploy/observe the Tron `IntentGatewayV2` at address `G`; no `_instances[keccak256(sourceChainX)]` entry exists for some state machine `X`.
2. Cause (or wait for) an ISMP `PostRequest` to be delivered to `G.onAccept` with `request.source = X` and `request.from = abi.encodePacked(G)` (20 bytes equal to the gateway's own address), body encoding `RequestKind.RedeemEscrow` for a real, previously-placed order commitment.
3. `authenticate(request)` computes `instance(X)`. Since `_instances[keccak256(X)] == address(0)`, it returns `address(this) == G`, which equals `module = address(bytes20(request.from)) == G`. The check passes.
4. `onAccept` proceeds to execute the escrow-release logic for the given commitment, transferring escrowed input tokens to the beneficiary specified in the forged request body, without any legitimate fill/cancellation having occurred on chain `X`. [1](#0-0)

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```
