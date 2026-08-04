Confirmed: `_params.dispatcher` in `IntentGatewayV2.sol` is a **single shared `CallDispatcher` instance** (`evm/src/utils/CallDispatcher.sol`), not a per-order/per-user contract. `CallDispatcher` is a generic, stateless relay with a `receive() external payable {}` and a `dispatch()` that executes arbitrary `Call[]` — it has no access control tying it to a specific order or caller.

### Title
Shared `CallDispatcher` balance is fully swept into any attacker's order escrow, letting one user steal native tokens left in transit by another order - (File: `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`placeOrder`'s predispatch-escrow branch sweeps the **entire current native balance** of the shared `CallDispatcher` into the caller's own escrow accounting, rather than only the amount that caller actually deposited for that specific call.

### Finding Description
When `order.predispatch.call.length > 0 && order.predispatch.assets.length > 0`, `placeOrder` sends the caller's native assets to the shared dispatcher, invokes `order.predispatch.call` (fully attacker-controlled calldata against a stateless, unauthenticated relay), then sweeps native balance back: [1](#0-0) 
```
if (token == address(0)) {
    uint256 balance = address(dispatcher).balance;
    if (balance < requiredAmount) revert InsufficientNativeToken();
    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
```
The sweep amount is `address(dispatcher).balance` — the dispatcher's *entire* current balance — not `requiredAmount` or any value scoped to this caller's contribution. `CallDispatcher` itself is a bare, stateless forwarder with no per-caller escrow or ownership tracking: [2](#0-1) 

Because it is one shared singleton address across all `placeOrder` invocations (and also reused by `ExtrinsicIntents`/`IntrinsicIntents` `_execute()` output-sweep path, and by `WrappedHyperFungibleToken.onAccept`'s calldata execution), any native token that is transiently sitting in the dispatcher — e.g., another order's in-flight predispatch funds within the same transaction bundle, a relayer/aggregator batching multiple `placeOrder` calls atomically, dust from a prior failed/partial sweep, or funds pushed by an unrelated `send()`/`onAccept()` call that also routes through the same dispatcher via `data.length > 0` — becomes fully sweepable by the next caller whose predispatch call executes while that balance is present. The `received > order.inputs[i].amount` "dust" branch does not return the excess to its rightful owner; it simply logs `DustCollected` while the excess ETH is already inside `address(this)` (the gateway), and the current caller's own order is unaffected by it, meaning the value is effectively re-attributed/absorbed without any check that it belongs to this order.

Existing guards do not stop this: `nonReentrant` only blocks re-entrant calls into `placeOrder` itself within the same call stack — it does not prevent a second, unrelated `placeOrder` (or other function using the same dispatcher) from executing in an earlier or later position within the same transaction (e.g., via a multicall/router/relayer batching several `placeOrder` calls, or via `order.predispatch.call` itself invoking `IntentGatewayV2.placeOrder` again with attacker-controlled `_params.dispatcher` state left over from the outer call before the sweep completes). The `balance < requiredAmount` check only guards a lower bound, never an upper bound tying the swept amount to what this caller actually deposited.

### Impact Explanation
An attacker can construct a `predispatch.call` that does nothing (a no-op contract call) while simply declaring `requiredAmount` equal to their own tiny contribution; if the shared dispatcher happens to hold native token balance from another party's concurrent or prior operation within the same block/tx context, that balance is swept in and credited toward the attacker's own order's input escrow instead of the rightful depositor, resulting in loss of funds for the other party and unauthorized crediting for the attacker. This falls squarely under "stealing or loss of funds" / "wrong beneficiary or amount" in the bounty scope, since it is reachable by any unprivileged caller of `placeOrder` — no relayer, prover, or admin compromise required.

### Likelihood Explanation
Likelihood depends on whether any code path leaves native ETH balance on the shared `CallDispatcher` visible during another `placeOrder`'s sweep — e.g., contract wallets/aggregators bundling multiple `placeOrder` calls in one transaction, or dust remaining from a previous partial sweep. Because `CallDispatcher` is a shared, permissionless, stateless relay reused across `IntentGatewayV2`, `ExtrinsicIntents._execute`, `IntrinsicIntents`, and other apps' calldata execution, the attack surface for "another user's ETH transiently on the dispatcher" is broader than a single-user flow, making this a realistic (not purely theoretical) path once any two flows touching the dispatcher can be ordered within the same transaction or interleaved via a crafted `predispatch.call`.

### Recommendation
Track and sweep only the exact `requiredAmount` this order actually deposited — do not read and forward `address(dispatcher).balance` wholesale. Snapshot the dispatcher's balance immediately before depositing this order's assets and compute the delta caused solely by this order's own transfer, or better, deploy a fresh, single-use `CallDispatcher` (e.g., via `CREATE2`/minimal proxy) per order so no balance can ever be shared across unrelated callers. Any residual "dust" should be attributable and refundable to its actual depositor rather than opportunistically credited to whichever order sweeps next.

### Proof of Concept
1. Two `placeOrder` calls with `predispatch.call`/`predispatch.assets` set are included in the same transaction (e.g. via a relayer/aggregator batch, or one order's `predispatch.call` re-entrant-calls a helper that triggers another party's pending native deposit into the shared dispatcher before its own sweep).
2. Order A deposits `1 ETH` to the shared `dispatcher` at `evm/src/apps/IntentGatewayV2.sol:216` and has not yet completed its sweep (lines 229–256) when Order B (attacker) executes its own predispatch flow against the same dispatcher.
3. Attacker's Order B declares `requiredAmount = 0.01 ETH`; at the sweep step (`evm/src/apps/IntentGatewayV2.sol:238`), `address(dispatcher).balance` reads `1.01 ETH` (A's 1 ETH + B's 0.01 ETH still present), and the full `1.01 ETH` is transferred to `address(this)` and credited toward B's escrow bookkeeping via `balancesBefore`/`received` accounting.
4. Order A's subsequent sweep now finds `dispatcher.balance == 0`, reverts with `InsufficientNativeToken`, and A's ETH is unrecoverable from that flow while B has captured the balance.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L237-241)
```text
                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
```

**File:** evm/src/utils/CallDispatcher.sol (L25-62)
```text
contract CallDispatcher is ICallDispatcher {
    /**
     * @dev error thrown when the target is not a contract.
     */
    error NotContract(address target);

    /**
     * @dev error thrown when a call fails.
     */
    error CallFailed(address target, bytes result);

    /**
     * @dev Receive function to accept ETH transfers
     */
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
    }
```
