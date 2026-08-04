### Title
`CallDispatcher.dispatch()` is a fully unauthenticated public entrypoint — any residual token/ETH balance left on the shared dispatcher can be drained by anyone - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
The Splits bug reduces to: a contract designed to be invoked only as part of a controlled callback flow (`verifyCallback`) actually exposes a *second*, unrestricted entrypoint (`execCalls`) that lets anyone perform the same privileged action outside of that flow, draining any funds the contract holds. `CallDispatcher` in Hyperbridge exhibits the identical pattern: it is a shared, persistent contract that legitimately holds tokens transiently during `placeOrder`/`fillOrder` predispatch/postdispatch execution, but its `dispatch()` function has **no access control whatsoever** — no `onlyGateway`, no caller check, nothing.

### Finding Description
`CallDispatcher.dispatch()` is declared `external` with zero authorization checks: [1](#0-0) 

It is meant to be called only mid-transaction by `IntentGatewayV2`/`IntentsBase`, which transfers assets to the dispatcher, invokes `dispatch()`, and then sweeps the resulting balances back: [2](#0-1) 

The postdispatch sweep in `_execute` only accounts for tokens explicitly declared in `order.output.assets` (`outputsLen`), not for any other token that the executed calldata (`order.output.call`, fully attacker/solver controlled since it comes from the `Order` struct) might have produced or routed through: [3](#0-2) 

Because `CallDispatcher` is a single shared singleton contract used across `IntentGatewayV2`, `HyperFungibleToken`, and `WrappedHyperFungibleToken` deployments (per the docs, "Existing `CallDispatcher` deployments are listed on the contract addresses page"), any dust, unswept intermediate-swap output, or ETH balance left on it after one order's execution persists in that contract's balance across transactions. Since `dispatch()` performs `to.call{value: call.value}(call.data)` using the dispatcher's *own* balance and has no caller restriction, any unprivileged address can call `CallDispatcher.dispatch()` directly with a `Call[]` that transfers out any ERC20 balance or ETH balance currently sitting on the dispatcher — completely bypassing the intended "only called from inside a placeOrder/fillOrder transaction" invariant, exactly as `execCalls()` bypassed the intended "only called via a legitimate swap callback" invariant in the Splits report.

### Impact Explanation
Any token or native-ETH balance that legitimately but unintentionally remains on the shared `CallDispatcher` (e.g., an intermediate swap output token not listed in `order.output.assets`, rounding dust, or a partially-completed sweep) is permanently and trivially stealable by any unprivileged caller, satisfying the bounty's "stealing or loss of funds" and "unauthorized transaction or execution" criteria. This requires no malicious relayer, prover, or admin — a plain EOA calling `CallDispatcher.dispatch()` is sufficient.

### Likelihood Explanation
`CallDispatcher` is a shared, address-listed singleton reused by multiple apps (`IntentGatewayV2`, `HyperFungibleToken`, `WrappedHyperFungibleToken`), all of which route tokens through it before sweeping. Any edge case that leaves tokens un-swept (fee-on-transfer intermediate tokens, multi-hop swaps in `predispatch`/`postdispatch` calldata producing tokens outside the declared `assets` list, or a revert mid-sweep in one integration) creates an immediately and permanently exploitable balance, because the drain path (calling `dispatch()` directly) requires no special timing, front-running, or privileged role — it can be executed at any point after the balance appears.

### Recommendation
Restrict `CallDispatcher.dispatch()` to be callable only by an authorized/registered caller (e.g., an allowlist of gateway/token contracts, or a per-call caller-bound context set atomically by the calling contract), or make `CallDispatcher` non-persistent by deploying an ephemeral instance per call (e.g., via `CREATE2`/minimal proxy that self-destructs or is single-use), so it can never hold a balance beyond the boundaries of one legitimate transaction. Additionally, ensure `_execute`'s sweep logic accounts for *all* tokens the postdispatch/predispatch calldata could plausibly produce, not just the declared `order.output.assets`/`order.inputs`.

### Proof of Concept
1. A solver fills a cross-chain order whose `order.output.call` swaps output tokens through an intermediate token `X` not listed in `order.output.assets` (e.g., a multi-hop route where the router leaves a small `X` balance on the dispatcher due to slippage).
2. `IntentsBase._execute` sweeps only `order.output.assets` tokens back to the gateway; the leftover `X` balance remains on the shared `CallDispatcher` contract.
3. Any attacker (no relationship to the order, no relayer/prover role) calls `CallDispatcher.dispatch(abi.encode([Call({to: X, value: 0, data: abi.encodeWithSelector(IERC20.transfer.selector, attacker, balance)})]))` directly.
4. `CallDispatcher.dispatch()` has no caller check, so the call succeeds and the attacker steals the `X` balance — funds that belonged to the protocol/users, drained via a public entrypoint that was never supposed to be callable outside the gateway's own transaction.

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
