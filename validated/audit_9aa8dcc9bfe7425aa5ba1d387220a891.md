## Finding

CallDispatcher's `dispatch()` entrypoint has no access control, and the IntentGatewayV2 sweep logic only recovers tokens listed in `order.output.assets` — any other token that ends up in the shared CallDispatcher is permanently drainable by an unprivileged third party.

### Title
Unrestricted `CallDispatcher.dispatch()` lets any external caller sweep funds stranded in the shared dispatcher after order fills - (File: `evm/src/utils/CallDispatcher.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
The Sablier report's core defect is a shared execution/custody contract whose address is trusted implicitly and which is not access-restricted, letting an unrelated actor divert funds that transiently pass through it. Hyperbridge's IntentGatewayV2 has a structurally identical component: a single, protocol-wide `CallDispatcher` singleton that receives solver-delivered output tokens and executes user-supplied post-fill calldata against it, then attempts to "sweep" the residue back to the gateway. `CallDispatcher.dispatch()` itself carries no `onlyOwner`/`restrict` gate, and the gateway's own sweep only accounts for the tokens declared in `order.output.assets`.

### Finding Description
`CallDispatcher.dispatch(bytes encoded)` is `external` with no caller restriction: [1](#0-0) 

It is a single shared instance referenced via `_params.dispatcher` for the whole IntentGatewayV2 deployment (not created per-order), and is invoked from `_execute()` to run the order creator's arbitrary `order.output.call` against arbitrary targets, using whatever balance currently sits in the dispatcher: [2](#0-1) 

After running the user-supplied calls, the gateway sweeps the dispatcher's balance back to itself — but only for the tokens enumerated in `order.output.assets` (the loop bound is `outputsLen`, i.e. `order.output.assets.length`): [3](#0-2) 

Because `order.output.call` is fully attacker-controlled calldata executed against arbitrary `to` addresses (subject only to `extcodesize(to) != 0` in `CallDispatcher.dispatch`), an order creator can construct a postdispatch call sequence that causes a token *not* present in `order.output.assets` to land in the CallDispatcher (e.g., a swap leg that outputs a different token than declared, a partial/failed leg, refunded dust, or an airdrop/claim call). The gateway's sweep never looks at that token, so it is left stranded in the CallDispatcher. Since `dispatch()` has no access control, any unrelated, unprivileged address can subsequently call `CallDispatcher.dispatch()` directly with a `Call[]` such as `{to: strandedToken, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)}` and pull the entire stranded balance to themselves — this requires no relayer, prover, admin, or governance action, only a public call to a contract with no gating.

### Impact Explanation
This allows unauthorized transfer of funds that were destined for the intent-gateway (as protocol dust) or for the order's beneficiary, to an arbitrary unprivileged attacker — a direct loss-of-funds / wrong-beneficiary outcome. Because the CallDispatcher is a single shared singleton for the entire gateway, any residue from any order (from any user or solver) becomes a race for whoever calls `dispatch()` first, which is a public, permissionless function.

### Likelihood Explanation
Likelihood is moderate to high: exploitation only requires (1) constructing a postdispatch order whose calls cause an off-list token/ETH balance to remain in the dispatcher (fully within the order-placer's own control, e.g. a partially-filling multi-hop swap route or a call that mints/claims an incidental token), and (2) monitoring the dispatcher's balance and calling `dispatch()` before the legitimate sweep — but note the legitimate sweep runs later within the very same `fillOrder` transaction for the declared assets, so the attacker's real target is specifically balances the protocol's own sweep never enumerates. No privileged role or off-chain trust assumption is needed to call `CallDispatcher.dispatch()`.

### Recommendation
- Restrict `CallDispatcher.dispatch()` to the intended caller (e.g., only the configured `IntentGatewayV2`/gateway contracts), removing the fully public entrypoint.
- Make the CallDispatcher per-order/ephemeral (e.g., deployed via CREATE2 per commitment, or self-destructing/zeroed after use) instead of a single shared, stateful-during-execution singleton, so no cross-order or off-list token balance can accumulate for an outside party to claim.
- Have `_execute()`'s sweep enumerate the dispatcher's *entire* balance for every token actually touched by the postdispatch calls (or require the CallDispatcher to refuse to retain any non-zero balance at the end of `dispatch()`), rather than only the tokens listed in `order.output.assets`.

### Proof of Concept
1. Order creator places an order whose `output.call` is a multi-step swap routed through the CallDispatcher such that, in addition to the declared `output.assets` token, a small amount of an undeclared token `X` (e.g. a reward/airdrop token from the swap route, or unspent input from an intermediate hop) ends up held by the CallDispatcher.
2. Solver fills the order; `_execute()` runs the calls and then sweeps only `order.output.assets` tokens back to the gateway, per the loop shown at `evm/src/apps/intentsv2/IntentsBase.sol:444-473`. Token `X`'s balance is left inside `CallDispatcher`.
3. Any third party (no relationship to the order) calls `CallDispatcher.dispatch(abi.encode([Call({to: X, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, IERC20(X).balanceOf(dispatcher))})]))` directly — this succeeds because `dispatch()` in `evm/src/utils/CallDispatcher.sol:44-62` has no caller restriction — and the attacker receives the stranded balance. [1](#0-0) [4](#0-3) 

**Uncertainty note:** I was unable to locate and inspect the `placeOrder`/predispatch execution path (which docs describe as also routing `DispatchInfo.assets` through the same `CallDispatcher` before escrow) within the indexed code — my searches for `predispatch`/`DispatchInfo` in `evm/src/apps/intentsv2/*.sol` only matched struct/doc references, not the executing function body, which likely lives in `evm/src/apps/IntentGatewayV2.sol` or `IntentGatewayV2.sol`'s `placeOrder`. That code path may have its own sweep with the same or different completeness guarantees, which would affect the full severity/likelihood picture. If a more thorough audit is needed, a Devin session should read `evm/src/apps/IntentGatewayV2.sol` in full to confirm whether predispatch has an equivalent gap and whether `CallDispatcher` is truly a single global singleton versus deployed fresh per gateway instance.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-62)
```text
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-485)
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

            unchecked {
                ++i;
            }
        }

        if (sweepCount > 0) {
            Call[] memory finalCalls = new Call[](sweepCount);
            for (uint256 i; i < sweepCount;) {
                finalCalls[i] = sweepCalls[i];
                unchecked {
                    ++i;
                }
            }
            ICallDispatcher(dispatcher).dispatch(abi.encode(finalCalls));
        }
    }
```
