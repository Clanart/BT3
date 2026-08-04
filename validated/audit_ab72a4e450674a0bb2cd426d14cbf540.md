Based on the code evidence, I found a concrete, provable analog in the `CallDispatcher` contract used by `IntentGatewayV2`.

### Title
Permissionless `CallDispatcher.dispatch()` allows draining any residual token/ETH balance left behind by other users' predispatch/postdispatch calldata - (File: evm/src/utils/CallDispatcher.sol)

### Summary
`CallDispatcher` is a single, shared, persistent contract used by every `IntentGatewayV2` order for `predispatch` (swap-before-escrow) and `postdispatch` (fill-then-act) calldata execution. Its `dispatch()` function has **no access control** and its `receive()` accepts value from anyone. `IntentGatewayV2`/`IntentsBase` only sweep back the *specific* tokens listed in `order.inputs`/`order.output.assets` after a dispatch, using the dispatcher's raw `balanceOf`/`.balance` as the trusted deposit signal (`evm/src/apps/IntentGatewayV2.sol:237-251`, `evm/src/apps/intentsv2/IntentsBase.sol:438-485`). Any token produced as a byproduct of another user's arbitrary `predispatch.call`/`output.call` that is *not* one of that order's declared input/output tokens is never swept and sits on the shared `CallDispatcher` indefinitely. Because `dispatch()` is unguarded, any unprivileged third party can call it directly to sweep that residual balance to themselves.

### Finding Description
`CallDispatcher.dispatch()`: [1](#0-0) 
has no `onlyGateway`/`onlyOwner` modifier - it decodes an arbitrary `Call[]` and executes each entry with the caller-chosen `to`, `value`, and `data`, drawing native ETH from the dispatcher's own balance. Its `receive()` is likewise unrestricted.

`IntentGatewayV2.placeOrder`'s predispatch flow transfers `order.predispatch.assets` into the dispatcher, executes the user-supplied `order.predispatch.call` (an arbitrary swap/unwrap, e.g. Uniswap), and then sweeps back only the tokens explicitly enumerated in `order.inputs`, based on the dispatcher's *current total* balance for each: [2](#0-1) 
Any token or ETH the predispatch call produces that is not one of `order.inputs`' tokens (multi-hop swap dust, referral tokens, LP remainder, over-swept surplus) is left on the dispatcher.

The same shared-sweep pattern applies to postdispatch/`_execute`, which explicitly emits `DustCollected` for whatever remains on the dispatcher only for tokens in `order.output.assets`, and only when that specific order's fill invokes `_execute`: [3](#0-2) 

Because `dispatch()` is permissionless, any address can directly call `CallDispatcher.dispatch()` with a `Call{to: residualToken, data: transfer(attacker, balance)}` (or `Call{to: attacker, value: ethBalance}`) to seize any token/ETH sitting on the dispatcher that was never accounted for by another order's own token list - regardless of who deposited it or through which order's calldata it arrived. This mirrors the fractional-vault bug exactly: the system trusts a **raw shared balance** (`balanceOf(dispatcher)` / `dispatcher.balance`) instead of a **tracked, per-depositor amount**, and nothing gates who may act on that balance.

### Impact Explanation
Any leftover token/ETH balance produced by another user's swap/predispatch/postdispatch calldata on the shared `CallDispatcher` can be permissionlessly stolen by any third party who notices it and calls `dispatch()` directly - before the protocol's own next sweep or governance-only `SweepDust` action can claim it. This is unauthorized transfer of funds that were never meant for the caller, matching the required "loss of funds"/"unauthorized transfer" impact gate.

### Likelihood Explanation
Any order using `predispatch.call` or `output.call` that swaps through a DEX router, unwraps LP tokens, or interacts with any external protocol producing a byproduct token not in the order's declared token list creates exploitable residue. An attacker only needs to monitor `CallDispatcher`'s balance (a single fixed, well-known address shared by the whole protocol) and call `dispatch()` themselves - no special privileges, timing races with relayers, or governance access required.

### Recommendation
Restrict `CallDispatcher.dispatch()` to be callable only by the `IntentGatewayV2` (or a registered set of gateway/host addresses), and/or have the gateway sweep the dispatcher's *entire* balance for every token touched by predispatch/postdispatch calldata (not just the tokens explicitly declared in `order.inputs`/`order.output.assets`) back to the gateway/protocol treasury within the same atomic call, so no residual balance can ever persist on the shared dispatcher between transactions.

### Proof of Concept
1. User A places an order with `predispatch.call` that swaps ETH via Uniswap to DAI (the declared `order.inputs` token), but the swap path or router behavior leaves a small residual of an intermediate token (e.g., WETH dust, or a reward/referral token) on the `CallDispatcher`.
2. `IntentGatewayV2.placeOrder`'s sweep loop only builds `transferCalls` for tokens present in `order.inputs` (`evm/src/apps/IntentGatewayV2.sol:232-256`), so the intermediate token balance is never swept and remains on `CallDispatcher`.
3. An attacker (unrelated to User A's order) observes `CallDispatcher`'s balance and calls `CallDispatcher.dispatch(abi.encode([Call({to: intermediateToken, value: 0, data: transfer(attacker, balance)})]))` directly - no access control prevents this (`evm/src/utils/CallDispatcher.sol:44-61`).
4. The attacker receives tokens that belonged to the protocol/User A's transaction byproduct, with no relationship to any order they placed or filled.

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L227-256)
```text
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Build sweep calls and snapshot gateway balances before the sweep.
            Call[] memory transferCalls = new Call[](inputsLen);
            uint256[] memory balancesBefore = new uint256[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;

                if (token == address(0)) {
                    uint256 balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                    balancesBefore[i] = address(this).balance;
                } else {
                    uint256 balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                    balancesBefore[i] = IERC20(token).balanceOf(address(this));
                }

                unchecked {
                    ++i;
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
