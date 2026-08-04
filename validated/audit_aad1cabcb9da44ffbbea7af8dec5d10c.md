Based on my analysis, the strongest local analog to the "Unsafe Arbitrary Call" class is the shared, un-isolated `CallDispatcher` used by `IntentGatewayV2`/`IntentsBase` for predispatch and postdispatch calldata execution.

### Title
Shared singleton `CallDispatcher` allows a planted ERC20 approval to drain in-transit escrow of unrelated orders - (File: `evm/src/utils/CallDispatcher.sol`)

### Summary
`CallDispatcher.dispatch()` executes fully attacker/order-controlled `Call[]` arrays (`to`, `value`, `data`) with the dispatcher itself as `msg.sender` [1](#0-0) . Exactly like `OpportunityAdapter._callTargetContract` in the external report, this is an unrestricted arbitrary call primitive. The remediation prescribed for that bug — deploying a fresh, per-user instance via `Create2` so no allowance/state is shared across users — was not applied here: `_params.dispatcher` is a single, permanent address reused by every `placeOrder`/`fillOrder` call across all users, both for `predispatch` (before escrow) and `postdispatch`/`order.output.call` (after fill) [2](#0-1) [3](#0-2) .

### Finding Description
Because the dispatcher is a shared singleton, any order creator can embed a `Call` in `order.predispatch.call` (or `order.output.call`) that calls `approve(attacker, type(uint256).max)` on an arbitrary ERC20 token, executed with the dispatcher as the approving `msg.sender` [1](#0-0) . Nothing in `CallDispatcher` or in `IntentsBase._execute` / `IntentGatewayV2.placeOrder` restricts the target, selector, or resets approvals after use — the docs themselves acknowledge the dispatcher "holds tokens temporarily during execution" and warn callers to use exact, not unlimited, approvals, which is only advisory, not enforced on-chain [4](#0-3) .

Once such an approval is planted, it persists indefinitely on the one shared dispatcher address. Any other order that later routes the same token through the dispatcher — e.g. a victim's `predispatch` flow that moves real escrow tokens into the dispatcher before the post-call sweep, or a solver's `order.output.call` fill — creates a window where the dispatcher (`to`) holds a real balance while an unrelated attacker (`spender`) still holds a standing allowance on that token. The sweep-back logic only checks `IERC20(token).balanceOf(dispatcher)` and moves whatever is present, without verifying provenance [5](#0-4) . If the predispatch/postdispatch call itself hands control back to the caller mid-execution (e.g. a flash-swap-style callback or an ERC-777/hook token, both of which the docs explicitly describe as intended dispatcher use cases such as "swap-then-escrow" via Uniswap) [6](#0-5) , the attacker can call `transferFrom(dispatcher, attacker, balance)` directly on the token using the previously planted allowance, pulling funds that belong to the in-flight order rather than their own.

### Impact Explanation
An attacker who has planted a standing approval on the shared `CallDispatcher` can redirect another user's escrowed input tokens (or a solver's just-delivered output tokens) to themselves during the narrow window between transfer-in and sweep-out, causing direct loss of bridged/escrowed funds and incorrect settlement beneficiary — matching the bounty's "stealing or loss of funds" and "transaction manipulation" categories.

### Likelihood Explanation
Exploitation requires (1) the attacker to first place a low-cost order that plants the approval, and (2) a victim order or fill whose `predispatch`/`postdispatch` calldata target yields control back to an external party mid-call (e.g. a hook token or flash-swap style DEX interaction — patterns the protocol's own documentation encourages for "swap-then-escrow" flows). This is a real, non-privileged, unprivileged-attacker path, but it is conditioned on a reentrancy-style callback occurring within another party's own calldata, which I could not fully confirm is reachable against an unwilling victim purely from the code reviewed. This precondition should be verified further (e.g., whether any registered predispatch/postdispatch target in practice yields control back to the dispatcher's caller).

### Recommendation
Isolate the dispatcher per order/user (e.g., deterministic `Create2` per-order ephemeral dispatcher, as the original report's remediation prescribes), or at minimum: (1) reset/revoke any approvals granted during `dispatch()` at the end of the call, (2) restrict callable selectors/targets from order calldata (disallow `approve`/other allowance-granting calls), and (3) reentrancy-guard the entire predispatch/postdispatch-and-sweep sequence, not just the top-level `placeOrder`/`fillOrder` entrypoints.

### Proof of Concept
1. Attacker calls `placeOrder` with a trivial `predispatch.assets` amount and `predispatch.call = [{ to: TOKEN, value: 0, data: approve(attacker, type(uint256).max) }]`; this is dispatched with `dispatcher` as `msg.sender`, planting an unlimited allowance for `attacker` on `TOKEN` from the dispatcher [2](#0-1) .
2. A victim later places (or fills) an order whose `predispatch`/`output.call` uses `TOKEN` and includes a call to a target that yields control back mid-execution (e.g., a flash-swap callback or hook token), while `TOKEN` sits temporarily on the dispatcher awaiting the sweep-back transfer [7](#0-6) .
3. During that callback, attacker calls `TOKEN.transferFrom(dispatcher, attacker, balanceOf(dispatcher))` using the standing allowance from step 1, diverting the victim's in-transit tokens before the gateway's sweep captures them.

### Citations

**File:** evm/src/utils/CallDispatcher.sol (L44-61)
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L203-227)
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L438-467)
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
```

**File:** docs/content/developers/evm/hyper-fungible-token/overview.mdx (L94-98)
```text
### Security

The `CallDispatcher` executes calls in its own context (not via `delegatecall`), so the HFT contract's storage is never at risk. If any call in the array reverts, the entire `onAccept` handler reverts — including the token mint/unlock. The request can then be retried by any relayer until the timeout expires. If no successful execution occurs before the timeout, the request times out and the sender is eligible for a refund on the source chain. Token approvals in the `Call[]` should use exact amounts rather than unlimited allowances, since the dispatcher contract holds tokens temporarily during execution.

Existing `CallDispatcher` deployments are listed on the [contract addresses](/developers/evm/contract-addresses/mainnet) page.
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L97-99)
```text
### Predispatch

The `predispatch` field in `Order` contains calldata to execute *before* escrowing inputs. The predispatch assets specified in `DispatchInfo.assets` are transferred to the `CallDispatcher`, the encoded calls are executed, and the resulting tokens are transferred back to the gateway for escrow. This enables swap-then-escrow patterns — for example, a user sends ETH which the `CallDispatcher` swaps to DAI on Uniswap, and the resulting DAI is escrowed as the order input.
```
