### Title
`_execute()` in IntentGatewayV2 sweeps residual balances only for declared `order.output.assets`, permanently stranding any other token type produced by the output calldata on the shared `CallDispatcher` - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
This is the closest local analog to the reported `swap1155`/`_mint1155` class of bug: a function that assumes the "type" of asset it will receive back is fixed to a declared set, and only handles/sweeps that declared set — silently leaving any other asset type or address stuck on the intermediary contract when the actual on-chain execution doesn't match the assumption.

### Finding Description
`Order.output.call` is user-supplied calldata (set at order placement on the source chain, per `docs/content/developers/evm/intent-gateway/placing-orders.mdx`) that gets executed via `CallDispatcher` on the destination chain after a solver fills the order. The sweep-back logic in `_execute()` only iterates over the `outputsLen` entries taken from `order.output.assets` (the tokens the *user declared* the order deals in) and sweeps back only the balance of exactly those token addresses: [1](#0-0) 

```solidity
function _execute(Order calldata order, uint256 outputsLen) internal {
    if (order.output.call.length == 0) return;
    address dispatcher = _params.dispatcher;
    ICallDispatcher(dispatcher).dispatch(order.output.call);
    ...
    for (uint256 i; i < outputsLen;) {
        address token = address(uint160(uint256(order.output.assets[i].token)));
        ...
        uint256 balance = IERC20(token).balanceOf(dispatcher);
        if (balance > 0) { ... sweep ... }
    }
    ...
}
```

If the user's own `order.output.call` (e.g. a swap, unwrap, or LP-exit routed through the `CallDispatcher`) ends up producing or leaving a *different* token on the dispatcher than the ones enumerated in `order.output.assets` — analogous to the original bug where the contract assumed an ERC-1155 path while the vault was actually ERC-721 — that residual balance is never discovered or swept. `CallDispatcher` is a single shared, permissionless-dispatch contract used by every order (`_params.dispatcher`), so any token type not in the caller's declared `output.assets` list that lands there through a mis-specified or type-mismatched calldata path is permanently stuck: there is no generic "sweep any token" recovery function surfaced to users, and the existing `SweepDust`/dust-collection paths only apply to the `IntentGatewayV2` contract's own dust accounting, not to arbitrary residual balances on `CallDispatcher`.

This mirrors the reported pattern precisely: the contract "follows the declared path" (ERC-1155 vs ERC-721 in the original; the fixed `output.assets` token set here) while the actual execution can silently diverge, and the silent divergence causes tokens to be permanently frozen in a contract balance nobody can recover from.

### Impact Explanation
Funds (any ERC20 balance left on `CallDispatcher` outside the declared `order.output.assets` set) become permanently locked with no sweep path, matching the required "loss of funds" impact class. Because `CallDispatcher` is a single, shared contract across all orders, funds from one user's mis-specified calldata sit alongside unrelated orders' flows, increasing the blast radius of any stuck balance.

### Likelihood Explanation
Likelihood is low-to-medium and self-inflicted (a user configuring `order.output.call` incorrectly, or a call whose actual token output diverges from the declared `output.assets`, e.g. a swap that returns dust in an unlisted token, or execution paths that mint/return an unexpected asset). This directly parallels the original report's "low likelihood / high impact ⇒ medium severity" framing: an unprivileged actor's own operational mistake triggers a real, permanent freeze of value, not requiring any malicious peer, relayer, or admin.

### Recommendation
Do not rely solely on the caller-declared `output.assets` set to determine what to sweep back from `CallDispatcher` after executing `order.output.call`. Either:
- Require and verify that any token balances left on `CallDispatcher` after dispatch belong exclusively to the declared asset set (revert otherwise), or
- Provide a generic, permissionless "sweep any residual balance for a specific token" recovery function scoped to a specific order/commitment context so unexpected leftover assets are not unrecoverable, or
- Snapshot dispatcher's full token balance set before/after (for a bounded set of expected calldata targets) rather than trusting `order.output.assets` as the exhaustive list of tokens that could result from execution.

### Proof of Concept
1. User places a cross-chain order whose `order.output.assets` lists only Token A.
2. The user encodes `order.output.call` to route through `CallDispatcher` to perform a swap/unwrap on the destination chain that, due to a routing mistake or a token with non-standard behavior, actually deposits Token B (not Token A) onto the `CallDispatcher`.
3. A solver fills the order; `_execute()` dispatches the call, then only checks/sweeps `IERC20(TokenA).balanceOf(dispatcher)`.
4. Token B remains on `CallDispatcher` indefinitely — no code path exists to recover it, since the sweep loop in `_execute()` (`evm/src/apps/intentsv2/IntentsBase.sol:447-468`) is bounded strictly by `outputsLen`/`order.output.assets`.

Note: I was not able to fully verify whether `CallDispatcher.sol` (`evm/src/utils/CallDispatcher.sol`) exposes any owner-only or governance-gated sweep function for arbitrary tokens — the index did not return its full contents. If such a function exists, it would only be usable by governance/owner (not the end user), which does not eliminate the "permanent user-fund freeze from an operational mistake" impact but would change recovery-path characterization slightly. A Devin session with full repo access should confirm `CallDispatcher.sol`'s complete interface before finalizing remediation.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-468)
```text
    function _execute(Order calldata order, uint256 outputsLen) internal {
        if (order.output.call.length == 0) return;

        address dispatcher = _params.dispatcher;
        ICallDispatcher(dispatcher).dispatch(order.output.call);

        Call[] memory sweepCalls = new Call[](outputsLen);
        uint256 sweepCount = 0;

        for (uint256 i; i < outputsLen;) {
            address token = address(uint160(uint256(order.output.assets[i].token)));

            if (token == address(0)) {
                uint256 balance = dispatcher.balance;
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({to: address(this), value: balance, data: ""});
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            } else {
                uint256 balance = IERC20(token).balanceOf(dispatcher);
                if (balance > 0) {
                    sweepCalls[sweepCount] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    sweepCount++;
                    emit DustCollected(token, balance);
                }
            }
```
