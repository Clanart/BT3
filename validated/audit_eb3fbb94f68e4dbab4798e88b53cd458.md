### Title
Unauthenticated `CallDispatcher.dispatch()` allows theft of ETH/ERC20 balances left in the shared dispatcher by intent predispatch/postdispatch flows - ([File: evm/src/utils/CallDispatcher.sol])

### Summary
`CallDispatcher` is a shared, singleton utility contract used by `IntentGatewayV2` (and `HyperFungibleToken`/`WrappedHyperFungibleToken`) to execute arbitrary calldata during order predispatch/postdispatch. Its `dispatch()` function has **no access control whatsoever** — any address can call it directly and force the contract to send out any ETH or ERC20 balance it currently holds to an arbitrary recipient. Because the gateway's sweep-back logic after predispatch/postdispatch only recovers the specific token addresses declared in the order (`order.inputs` / `order.output.assets`), any native ETH or ERC20 dust that ends up in the dispatcher outside that declared set is left sitting in a permissionless contract that anyone can drain.

### Finding Description
`CallDispatcher.dispatch()` is defined with no caller restriction: [1](#0-0) 

It accepts an arbitrary `Call[]` and executes each call with attacker-chosen `to`, `value`, and `data`, requiring only that `to` have code. There is no `onlyGateway`, `onlyOwner`, or `msg.sender` check of any kind — unlike every other privileged mutator in the codebase (e.g. `onlyHost`, `onlyPCVController`-style guards seen elsewhere), this entrypoint is fully public.

`IntentGatewayV2.placeOrder` (and the analogous fill/`_execute` path in `IntentsBase`) uses this same dispatcher to run user-supplied predispatch/postdispatch calldata, funding it first and sweeping funds back afterward: [2](#0-1) 

Critically, the sweep-back loop iterates only over `order.inputs` (for `placeOrder`) or `order.output.assets` (for `_execute`), not over the dispatcher's full balance across all token types: [3](#0-2) 

If the predispatch/postdispatch calldata (which is user/solver supplied and can call arbitrary DEX routers, etc.) produces or leaves behind a token or native ETH amount that is **not** one of the declared `inputs`/`output.assets` types — e.g. excess native ETH sent to fund a swap that only partially consumes it, a swap that yields an unlisted intermediate token, or any rounding remainder — that balance is never swept back to the gateway. It simply remains on the `CallDispatcher` contract's balance, held by a contract whose `dispatch()` entrypoint anyone can call to route that exact balance to themselves via a single `Call{to: attacker, value: balance}`.

### Impact Explanation
This breaks the "funds move exactly once, only to the rightful beneficiary" invariant required for bridge custody/escrow: native ETH or tokens transiently custodied by the shared `CallDispatcher` during intent settlement can be permissionlessly redirected to an attacker rather than being swept back to the `IntentGatewayV2` for the order owner/solver. Since `CallDispatcher` is a shared, chain-wide singleton reused across many orders (and multiple gateway apps per the docs listing "existing CallDispatcher deployments"), any dust or unswept balance accumulated from any order's predispatch/postdispatch execution is a standing, unprivileged theft target — no malicious relayer, prover, or admin is required, only calling `dispatch()` directly.

### Likelihood Explanation
Predispatch/postdispatch calldata is explicitly designed for arbitrary DeFi composition (Uniswap swaps, LP unwraps, etc.) whose output amounts are not guaranteed to exactly match the declared `order.inputs`/`order.output.assets` token set (e.g., swap slippage, leftover native ETH not part of a token-only input list, or a multi-hop swap producing an intermediate token). Any such mismatch leaves value sitting on `CallDispatcher`, and because `dispatch()` requires no authorization, exploitation is a single unprivileged transaction with no timing race needed beyond "before the legitimate sweep, if any."

### Recommendation
- Add access control to `CallDispatcher.dispatch()` (e.g., an allowlist of authorized gateway/app callers, or restrict to `msg.sender == owner`), so arbitrary third parties cannot direct the contract's held balances.
- Alternatively/additionally, make the dispatcher non-custodial by design: never leave value on it across calls — sweep the *entire* balance of every token touched by predispatch/postdispatch calldata (not just the declared `inputs`/`output.assets` token list) back to the gateway before returning control, and revert if any unexpected token/ETH remains.
- Consider adding a `withdraw`/`sweep` recovery function restricted to a privileged role for any legacy dust already present, consistent with the original report's recommendation to provide a controlled recovery mechanism instead of relying on funds being inaccessible to anyone (including attackers).

### Proof of Concept
1. A user places an order via `IntentGatewayV2.placeOrder` with `predispatch.assets = [{token: address(0), amount: X}]` (native ETH) and `predispatch.call` that swaps only part of `X` (e.g., a Uniswap `swapETHForExactTokens` call that only spends `Y < X`), while `order.inputs = [{token: DAI, amount: ...}]` (no native-ETH entry in `inputs`).
2. `placeOrder` sends `X` ETH to `CallDispatcher`, calls `dispatch(predispatch.call)` which spends only `Y`, leaving `X - Y` ETH on the `CallDispatcher`.
3. The subsequent sweep loop in `placeOrder` (evm/src/apps/IntentGatewayV2.sol:230-258) only builds `transferCalls` for tokens in `order.inputs` (DAI), never for `address(0)` — the leftover `X - Y` ETH is never swept back.
4. An attacker calls `CallDispatcher.dispatch(abi.encode([Call({to: attacker, value: X - Y, data: ""})]))` directly — this succeeds because `dispatch()` has no access control — draining the leftover ETH to the attacker.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L36-62)
```text
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

**File:** evm/src/apps/IntentGatewayV2.sol (L203-258)
```text
        if (order.predispatch.call.length > 0 && order.predispatch.assets.length > 0) {
            address dispatcher = _params.dispatcher;

            uint256 assetsLen = order.predispatch.assets.length;
            for (uint256 i; i < assetsLen;) {
                address token = address(uint160(uint256(order.predispatch.assets[i].token)));
                uint256 amount = order.predispatch.assets[i].amount;
                if (amount == 0) revert InvalidInput();

                if (token == address(0)) {
                    if (amount > msgValue) revert InsufficientNativeToken();
                    msgValue -= amount;

                    (bool sent,) = dispatcher.call{value: amount}("");
                    if (!sent) revert InsufficientNativeToken();
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, dispatcher, amount);
                }

                unchecked {
                    ++i;
                }
            }

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

            ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls));
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-484)
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
```
