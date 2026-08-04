Confirmed: `_params.dispatcher` (`CallDispatcher`) is a **shared, singleton, persistent contract**, deployed once at gateway setup and reused across *every* `placeOrder` / `fillOrder` call by *every* user forever [1](#0-0) . It holds a `receive()` and simply forwards arbitrary `Call[]` from whoever the gateway tells it to [2](#0-1) . This confirms the analog is exploitable.

### Title
Predispatch native-token sweep forwards the CallDispatcher's *entire* balance instead of the required amount, permanently trapping other users' residual ETH - (File: `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder`'s predispatch flow mirrors the DODO `externalSwap` flaw: it uses an aggregate before/after balance check on a shared intermediary contract instead of tracking the exact amount that belongs to the current caller. For native-token (`address(0)`) inputs it sweeps the dispatcher's *full* current balance — not `order.inputs[i].amount` — into the gateway, then silently reclassifies anything above the requested amount as "dust" via an event, with no attribution and no return path to the party that actually owned that ETH.

### Finding Description
In `placeOrder`, when a predispatch call is present, per-input transfer calls are built as:

```solidity
if (token == address(0)) {
    uint256 balance = address(dispatcher).balance;
    if (balance < requiredAmount) revert InsufficientNativeToken();
    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
    balancesBefore[i] = address(this).balance;
}
``` [3](#0-2) 

Note `value: balance` — the *entire* dispatcher balance at snapshot time — is queued for transfer, not `requiredAmount`. After `ICallDispatcher(dispatcher).dispatch(...)` executes, the received amount is measured purely as a delta:

```solidity
if (token == address(0)) {
    received = address(this).balance - balancesBefore[i];
} else { ... }
if (received > order.inputs[i].amount) {
    uint256 dust = received - order.inputs[i].amount;
    emit DustCollected(token, dust);
} else {
    order.inputs[i].amount = received;
}
``` [4](#0-3) 

Because `_params.dispatcher` is one fixed, persistent, shared `CallDispatcher` instance used by every `placeOrder`/`fillOrder` call from every user across time [1](#0-0) [5](#0-4) , any native-ETH remainder left sitting on the dispatcher — from a prior user's over-funded predispatch call, a partially failed sweep, or any stray `receive()` deposit — is not owned by, or attributable to, the *next* caller. Yet the very next `placeOrder` call that happens to route a native-token input through predispatch will read `address(dispatcher).balance` (the *whole* balance, including that stranded ETH) and sweep 100% of it into the gateway via `transferCalls[i]`. The excess over the current order's `requiredAmount` is emitted as `DustCollected` and left stuck in `IntentGatewayV2`'s own balance — never credited to any commitment in `_orders`, and never returned to the original depositor. This is functionally identical to DODO's bug: an accounting check based on raw balance delta of a shared holding contract, rather than tracking the exact amount that belongs to the operation in progress, silently misattributes/loses value that isn't the current caller's.

This differs from the intentionally-designed `_execute` dust-sweep (post-fill), which the docs explicitly describe as sending 100% of solver overpayment to the protocol [6](#0-5)  — that path is a *documented* protocol-fee mechanism for the current order's own solver-supplied excess. The predispatch path in `placeOrder`, by contrast, has no such design rationale: it is meant only to convert/relay the user's own escrowed assets, not to absorb whatever else happens to be sitting in the shared dispatcher at that moment. There is no guard ensuring `balance` reflects only assets originating from *this* call's `predispatch.assets` transfer.

### Impact Explanation
Any ETH stranded on the shared `CallDispatcher` (e.g., from a reverted/partial predispatch sequence in an earlier transaction, or dust that legitimately belonged to another user) is unconditionally and irreversibly absorbed into `IntentGatewayV2`'s balance by the next unrelated `placeOrder` caller who triggers the native-token predispatch branch. The value is not credited to any order, is not returned to its rightful owner, and becomes generally trapped/unaccounted-for protocol balance — a direct loss-of-funds condition for whoever the ETH belonged to, triggered by an ordinary unprivileged user's normal `placeOrder` call.

### Likelihood Explanation
This requires no malicious relayer, prover, or governance actor — only two consecutive unprivileged `placeOrder` calls: one that leaves ETH stranded on the shared dispatcher (via a predispatch call that doesn't consume 100% of the forwarded ETH, plausible with any swap/DEX-style predispatch call that leaves rounding remainder), and a subsequent, entirely unrelated `placeOrder` call using a native-token input through the predispatch branch. Since `CallDispatcher` is a long-lived singleton reused indefinitely by the whole gateway [5](#0-4) , such residue-then-sweep sequences can occur naturally over the contract's lifetime without any attacker coordination, and can also be deliberately engineered by a user who front-loads dust before someone else's order to redirect their ETH into the protocol's stuck balance.

### Recommendation
Track the amount forwarded to the dispatcher for the *current* call explicitly (e.g., sum of `predispatch.assets` amounts actually attributable to this transaction) rather than reading `address(dispatcher).balance` wholesale. Sweep only `requiredAmount` (or the exact amount produced by this transaction's predispatch execution) back to the gateway, and if any genuine surplus exists from *this* transaction's own predispatch conversion, return it to `msg.sender` rather than silently trapping it as unattributed "dust" in the gateway contract.

### Proof of Concept
1. User A calls `placeOrder` with a predispatch call that swaps ETH via an external router but the router returns slightly less than 100% consumption, leaving `X` wei of ETH stranded on the shared `CallDispatcher` after A's transferCalls sweep completes (e.g., due to a `CallFailed` partial-batch condition or a router quirk that leaves rounding remainder unswept in a particular execution ordering).
2. User B, in a later unrelated transaction, calls `placeOrder` with `order.inputs[0].token == address(0)`, `amount = Y`, and a predispatch call/asset transfer of `Y` wei ETH to the dispatcher.
3. At the point `IntentGatewayV2` builds B's `transferCalls`, `balance = address(dispatcher).balance = Y + X` (B's own `Y` plus A's stranded `X`).
4. `transferCalls[0] = Call({to: address(this), value: Y + X, data: ""})` sweeps the *entire* `Y + X` into the gateway.
5. `received = Y + X`, which exceeds `order.inputs[0].amount = Y`; the code emits `DustCollected(address(0), X)` and leaves `X` permanently in `IntentGatewayV2`'s balance, uncredited to any order and unreturned to User A.

### Citations

**File:** evm/script/DeployIntentGateway.s.sol (L51-51)
```text
                    dispatcher: config.get("CALL_DISPATCHER").toAddress(),
```

**File:** evm/src/utils/CallDispatcher.sol (L39-61)
```text
    receive() external payable {}

    /**
     *  @dev reverts if the target is not a contract or if any of the calls reverts.
     */
    function dispatch(bytes memory encoded) external {
        Call[] memory calls = abi.decode(encoded, (Call[]));
        uint256 callsLen = calls.length;
        for (uint256 i = 0; i < callsLen; ++i) {
            Call memory call = calls[i];
            uint32 size;
            address to = call.to;
            assembly {
                size := extcodesize(to)
            }

            if (size == 0) {
                revert NotContract(to);
            }

            (bool success, bytes memory result) = to.call{value: call.value}(call.data);
            if (!success) revert CallFailed(to, result);
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L237-241)
```text
                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L264-275)
```text
                if (token == address(0)) {
                    received = address(this).balance - balancesBefore[i];
                } else {
                    received = IERC20(token).balanceOf(address(this)) - balancesBefore[i];
                }

                if (received > order.inputs[i].amount) {
                    uint256 dust = received - order.inputs[i].amount;
                    emit DustCollected(token, dust);
                } else {
                    order.inputs[i].amount = received;
                }
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L108-110)
```text
- **Cross-chain**: Calldata executes **immediately** after the solver delivers output tokens to the beneficiary, before the settlement message is dispatched back to the source chain.

After execution, any tokens remaining in the `CallDispatcher` are swept back to the gateway and collected as dust (emitting `DustCollected` for each token). When postdispatch calldata is present, 100% of any surplus (solver overpayment) goes to the protocol rather than being split with the beneficiary — this prevents manipulation of surplus distribution through calldata side effects.
```
