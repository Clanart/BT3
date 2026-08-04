## Title
`instance()` zero-address fallback in Tron IntentGatewayV2 lets an unregistered source chain authenticate as the gateway itself, enabling forged `RedeemEscrow`/`RefundEscrow` fund theft - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

## Summary

## Finding Description
`evm/tron/contracts/apps/IntentGatewayV2.sol` resolves the trusted peer gateway for a given source chain via:

```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    return gateway == address(0) ? address(this) : gateway;
}
``` [1](#0-0) 

and authenticates every inbound cross-chain post request against it:

```solidity
function authenticate(PostRequest calldata request) internal view {
    if (request.from.length != 20) revert InvalidInput();
    address module = address(bytes20(request.from));
    if (instance(request.source) != module) revert Unauthorized();
}
``` [2](#0-1) 

`_instances[keccak256(stateMachineId)]` defaults to `address(0)` for any `stateMachineId` that was never registered via a `NewDeployment` admin message. Instead of rejecting an unregistered source (as the intended design requires), `instance()` silently falls back to `address(this)` — the gateway's own address. This is structurally identical to the reported bug: `adapterToId[unregisteredAddr]` defaulting to `0`, which collided with a legitimately-registered `adapterId == 0` and made `isAdapterInitialized(0)` return true for any address. Here, an unregistered `stateMachineId` collides with the "self" identity because both resolve to `address(this)`.

This is a real regression relative to the primary EVM contract in this same repo: `evm/src/apps/intentsv2/IntentsBase.sol` explicitly defines an `UnknownInstance` error for exactly this case, and its test suite documents the intended behavior: "An unregistered chain reverts with UnknownInstance" [3](#0-2) . The Tron variant did not receive this hardening and still contains the vulnerable zero-default fallback.

## Impact Explanation
Since `IntentGatewayV2` is deployed via CREATE2 for deterministic same-address deployment across chains (this pattern is explicit elsewhere in the repo, e.g. the LayerZero adapter's "Assumes the same contract address on all chains (CREATE2 deployment)" [4](#0-3) ), the gateway's own address is identical on every EVM-compatible chain sharing the same factory/salt/bytecode. An attacker can:

1. Deploy the identical `IntentGatewayV2` bytecode (via the same CREATE2 factory and salt) on any EVM chain that Hyperbridge's infrastructure recognizes as a state machine but that the victim gateway has **not yet registered** as a peer in `_instances` (e.g. a newly supported or lesser-used chain). This reproduces `address(this)` == the victim's real address on the target chain.
2. From that shadow deployment, dispatch a forged ISMP `PostRequest` (`from` = the shadow contract's own address = the victim's address, `source` = the unregistered chain, `to` = the real victim gateway) carrying a `RedeemEscrow` or `RefundEscrow` body naming any existing order `commitment` with non-zero escrow, and an attacker-controlled `beneficiary`.
3. On the victim chain, `onAccept` calls `authenticate(request)` → `instance(request.source)` finds no registered entry (`address(0)`) → falls back to `address(this)` → matches `request.from` (also `address(this)`) → authentication **passes**, even though the source chain was never registered as a trusted peer.
4. `withdraw()` then releases the escrowed input tokens for that commitment straight to the attacker, without the attacker ever having legitimately filled the order or provided output tokens.

This is direct, unauthorized theft of user-escrowed bridge funds, matching the bounty's "stealing or loss of funds" and "false proof/state acceptance"/"logic attacks" impact categories, reachable by an unprivileged attacker with no relayer, prover, or admin compromise required.

## Likelihood Explanation
Likelihood is high wherever this Tron contract is deployed with the shared CREATE2 deployment pattern used elsewhere in the protocol, since: (a) `_instances` starts empty and grows only as `NewDeployment` messages arrive from Hyperbridge over time, so there is inherently a window (or indefinitely, for chains never intended to be integrated) where a given `stateMachineId` is unregistered; (b) the exploit requires no privileged role, relayer collusion, or governance compromise — only the ability to deploy a contract on a distinct chain Hyperbridge can produce state proofs for, and to submit a normal ISMP dispatch.

## Recommendation
Replace the fallback in `instance()` with an explicit revert for unregistered state machines, matching the already-corrected primary EVM implementation:

```solidity
function instance(bytes calldata stateMachineId) public view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    if (gateway == address(0)) revert UnknownInstance();
    return gateway;
}
```

Any legitimate need for the gateway to trust "itself" on its own home chain should be handled via an explicit self-registration entry in `_instances` at initialization (as done in the primary EVM contract, where `instance(bytes("SOURCE_CHAIN"))` is explicitly seeded to `address(gateway)` during atomic init [5](#0-4) ), not via an implicit zero-address fallback.

## Proof of Concept
1. Victim `IntentGatewayV2` (Tron variant) is deployed at address `G` on Chain A with a legitimately placed order whose commitment `C` has non-zero escrow in `_orders[C][token]`.
2. Attacker deploys the identical `IntentGatewayV2` bytecode via the same CREATE2 factory/salt on Chain X (a chain Hyperbridge's ISMP host can produce/verify state proofs for, but which was never registered in the victim's `_instances`), landing at the same address `G`.
3. From address `G` on Chain X, attacker's dispatch triggers an ISMP `PostRequest` with `from = G`, `source = ChainX`, `to = G` (Chain A), `dest = ChainA`, body = `RedeemEscrow` + `WithdrawalRequest{commitment: C, tokens: [...], beneficiary: attacker}`.
4. Victim's `onAccept` → `authenticate()` computes `instance(ChainX)` → `_instances[keccak256(ChainX)] == address(0)` → returns `address(this) == G` → matches `request.from == G` → authentication succeeds.
5. `withdraw()` transfers the escrowed tokens for commitment `C` to `attacker`, confirmed by the check `if (_orders[body.commitment][token] == 0) revert UnknownOrder();` passing for the real, non-zero escrow [6](#0-5) , and funds are stolen without any legitimate fill occurring on Chain X.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L278-284)
```text
    /**
     * @dev Fetch the IntentGateway contract instance for a chain.
     */
    function instance(bytes calldata stateMachineId) public view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        return gateway == address(0) ? address(this) : gateway;
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L286-294)
```text
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L691-691)
```text
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L2896-2901)
```text
    function testInstance() public {
        bytes memory stateMachineId = bytes("TEST_CHAIN");

        // An unregistered chain reverts with UnknownInstance.
        vm.expectRevert(IntentsBase.UnknownInstance.selector);
        intentGateway.instance(stateMachineId);
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3798-3799)
```text
        assertEq(gateway.params().host, address(host), "params set via atomic init");
        assertEq(gateway.instance(bytes("SOURCE_CHAIN")), address(gateway), "peer bound to address(this)");
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L46-47)
```text
 * @dev Implements `ILayerZeroEndpointV2` for OApp compatibility and `HyperApp` for ISMP
 * message handling. Assumes the same contract address on all chains (CREATE2 deployment).
```
