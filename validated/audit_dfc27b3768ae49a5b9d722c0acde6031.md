## Finding

### Title
Stale `_instances` gateway mapping lets `cancelOrder` prove non-membership against the wrong destination contract, enabling refund of escrow already owed to a solver - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
This is the local analog of the reported bug class: a registry mapping (`name_to_addr`/`addr_to_name` in the C4 report, `_instances[keccak256(stateMachineId)] => gateway` in Hyperbridge's Intent Gateway) is mutated in place on re-registration, and a downstream function trusts the *current* value of that mapping instead of the value that was authoritative when the underlying commitment was created. In the report this let an old domain owner keep receiving funds; here it lets a stale/rotated destination-gateway lookup make `_cancelFromSource` believe an order was never filled, causing the source chain to refund escrow that the solver has already legitimately earned.

### Finding Description
`IntentsBase._addDeployment` (`evm/src/apps/intentsv2/IntentsBase.sol:521-524`) unconditionally overwrites the single global mapping used to resolve a chain's IntentGateway peer:

```solidity
function _addDeployment(Deployment memory body) internal {
    _instances[keccak256(body.chain)] = body.gateway;
    emit DeploymentAdded({chain: string(body.chain), gateway: body.gateway});
}
``` [1](#0-0) 

This mapping is not versioned or scoped per-order — it is looked up live, by every cross-chain code path, using only the chain id, never the order's placement time:

```solidity
function _instance(bytes calldata stateMachineId) internal view returns (address) {
    address gateway = _instances[keccak256(stateMachineId)];
    if (gateway == address(0)) revert UnknownInstance();
``` [2](#0-1) 

`_cancelFromSource` builds the ISMP `GET` storage-proof key against whatever `_instance(order.destination)` currently resolves to:

```solidity
bytes[] memory keys = new bytes[](1);
keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
DispatchGet memory request = DispatchGet({
    dest: order.destination,
    keys: keys,
    ...
``` [3](#0-2) 

The response handler trusts an *empty* storage slot as proof the order was never filled and immediately refunds escrow:

```solidity
function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
    if (incoming.response.values[0].value.length != 0) revert Filled();
    WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
    _withdraw(body, true, true);
}
``` [4](#0-3) 

`NewDeployment` handling (a normal, documented operational event — the codebase's own docs describe redeployments as expected: *"Overwriting an existing registration is allowed (useful for redeployments); in-flight purchases from the old contract will fail after the swap"*) is dispatched exactly the same way for the Intent Gateway's `_instances` map, with no guard for orders still in flight:

```solidity
if (kind == RequestKind.NewDeployment) {
    _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
``` [5](#0-4) 

Because `order.destination` only encodes the *state machine id*, and `_instance()` always resolves to whatever gateway is currently registered for that id, the storage-proof key used to check whether an order was filled is computed against the address that is live **at cancel time**, not the address that was live when the order was placed and (possibly) filled. If a gateway redeployment (`NewDeployment`) happens for the destination chain between order placement/fill and the source-side cancellation window (which the docs guarantee opens only after `order.deadline`, i.e. an intentionally-delayed window), `_instance(order.destination)` now points at the brand-new gateway contract, whose storage for that commitment slot is necessarily empty (it never processed that order). The GET response therefore reports "not filled" even though the order was in fact filled on the old (now-orphaned) gateway.

### Impact Explanation
`_cancelFromSource` is gated to the order's own `user` (`if (order.user != ... msg.sender) revert Unauthorized();`), so this is not a third-party theft, but it is a genuine loss-of-funds / wrong-beneficiary path that breaks the protocol's settlement guarantee: the solver who already delivered the destination-side output tokens to the beneficiary is entitled to the escrowed source-chain input tokens via the in-flight `RedeemEscrow` message. If the user manages to get a refund accepted first (via the stale-mapping GET-non-membership race), `_withdraw`'s `_orders[commitment][token] == 0` check zeroes out escrow on refund, so the later-arriving legitimate `RedeemEscrow` for the solver will revert with `UnknownOrder` — the solver never gets paid despite having correctly fulfilled the order. This is directly the C4 report's core invariant break: a registry entry that is silently repointed causes state that should have been immutable per-commitment (which gateway "owns" this order's destination-side record) to be resolved against the wrong instance, producing a wrong-beneficiary fund outcome. This maps to the required impact categories "unauthorized transaction/execution," "transaction manipulation," and "false proof/state acceptance" (a non-membership proof against the wrong contract is treated as authoritative).

### Likelihood Explanation
This does not require a malicious relayer, prover, or governance actor — `NewDeployment` (redeployment) is documented as a normal, periodically-expected operational event across the codebase (bandwidth manager, LZ endpoint, hyper-fungible-token all support and expect address rotation). The only requirements are: (1) an order is placed and filled cross-chain shortly before a scheduled/emergency gateway redeployment on the destination chain, and (2) the user calls `cancelOrder` from the source side during the window between the redeployment and the arrival of the `RedeemEscrow` settlement message (a window that can span the ISMP challenge period, `unStakingPeriod`, and message relay latency — often minutes to hours). An ordinary user acting purely in self-interest (reclaim funds early) can trigger this without any privileged access; the trigger condition is a normal governance/ops action, not an attacker-controlled one.

### Recommendation
Snapshot the destination gateway address (and/or a version/epoch counter) into the order or its commitment at placement time, and require `_cancelFromSource` (and any other query that must prove destination state for a specific order) to use that snapshotted address rather than `_instance(order.destination)` resolved at query time. Alternatively, retain historical `_instances` entries (e.g., `mapping(bytes32 chainHash => mapping(uint256 epoch => address))`) and have callers reference the epoch active when the order was placed, analogous to how the `initia` fix cleared/repointed stale name mappings atomically at re-registration instead of leaving old lookups silently reusable against unrelated state.

### Proof of Concept
1. User places a cross-chain order on chain A with destination chain B; solver fills it on chain B's `IntentGatewayV2` instance `G_old`, setting `_filled[commitment] = solver` in `G_old`'s storage and dispatching `RedeemEscrow` back to chain A.
2. Before the `RedeemEscrow` message is finalized/delivered on chain A (still within the ISMP challenge period), Hyperbridge governance performs a routine gateway redeployment for chain B, dispatching `NewDeployment` to chain A's gateway, which executes `_addDeployment` and sets `_instances[keccak256(chainB)] = G_new`.
3. After `order.deadline` passes, the user calls `cancelOrder` → `_cancelFromSource` on chain A. `_instance(order.destination)` now resolves to `G_new`. The dispatched `DispatchGet` queries the commitment's storage slot on `G_new`, which is empty (the order was never processed there).
4. Hyperbridge relayers/provers deliver a valid non-membership proof for `G_new`'s empty slot (this is not proof forgery — it correctly proves `G_new` never saw the order, but that is the wrong contract to ask). `onGetResponse` accepts it and calls `_withdraw`, refunding escrow to `order.user` and zeroing `_orders[commitment][token]`.
5. The delayed `RedeemEscrow` message from `G_old` later arrives on chain A; `_withdraw` reverts with `UnknownOrder` because escrow was already zeroed, permanently denying the solver their earned settlement even though they delivered on chain B.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-360)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L521-524)
```text
    function _addDeployment(Deployment memory body) internal {
        _instances[keccak256(body.chain)] = body.gateway;
        emit DeploymentAdded({chain: string(body.chain), gateway: body.gateway});
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L202-215)
```text
        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L299-300)
```text
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```
