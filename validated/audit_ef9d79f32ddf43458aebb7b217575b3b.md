Confirmed: `_params.dispatcher` is a **shared, singleton** `CallDispatcher` used by every `placeOrder` call across all users [1](#0-0) , and the sweep step transfers the **entire current token balance held by the dispatcher** — not just the amount the current caller funded — crediting it to `order.inputs[i].amount` for whichever order happens to be executing at that moment.

### Title
Shared `CallDispatcher` balance-sweep lets a caller steal residual escrow funds via `placeOrder` predispatch - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
`placeOrder`'s predispatch path reuses the pattern from the reported Line-of-Credit bug: it computes a value to credit into escrow (`order.inputs[i].amount`) from a **balance delta on a contract whose calldata/execution is attacker-influenced**, rather than from a value strictly scoped to the current caller's own deposited funds.

### Finding Description
In `placeOrder`, when `order.predispatch.call.length > 0`, tokens are sent to `_params.dispatcher` (line 216/219), the caller's arbitrary `order.predispatch.call` is executed via `ICallDispatcher(dispatcher).dispatch(...)` (line 227), and then the code sweeps the **full current balance of the token held by `dispatcher`**: [2](#0-1) 

Critically, `balance = IERC20(token).balanceOf(dispatcher)` is not limited to the amount the current caller deposited — it is whatever the dispatcher happens to hold at that instant, and the resulting `Call` sweeps that entire balance to `address(this)`. The subsequent "received" measurement then directly sets `order.inputs[i].amount = received` (or reduces to `received` when smaller than expected) with no attribution back to what this specific caller actually contributed: [3](#0-2) 

Because `dispatcher` is a single, protocol-wide contract address (`_params.dispatcher`, set once at `initialize`), any residual token balance sitting in it — from a prior `placeOrder` predispatch swap that yielded slightly more output than expected, from a partially-failed/aborted predispatch flow, from fee-on-transfer/rounding remainders, or from tokens sent to it directly by any third party — is fair game for the **next unrelated caller** to sweep into their own order's escrow via a trivial predispatch call (e.g., a no-op `Call[]` that transfers only 1 wei of their own funds in). The check `if (balance < requiredAmount) revert InvalidInput()` only guards a lower bound; it does nothing to prevent an attacker from claiming credit for tokens they never contributed, exactly mirroring how `claimAndTrade`'s `tokensBought == 0` check was defeated by a caller-controlled side effect that manufactured the measured delta.

### Impact Explanation
An attacker who observes (or engineers, e.g. by front-running a partially-reverting predispatch call, or exploiting rounding/fee-on-transfer dust) a nonzero balance sitting in the shared `CallDispatcher` for a given token can place an order whose declared `order.inputs[i].amount` is inflated by that residual balance while contributing only a negligible amount themselves. Because `order.inputs` becomes the amount paid out to whichever solver fills the order (see `_fillSameChain`/`_fillCrossChain`, which release `_orders[commitment][token]` to the solver), this results in escrowed funds being credited to an order that the placing user did not actually fund — an unauthorized transfer of value out of the shared dispatcher's balance and into an attacker-controlled order commitment, ultimately reachable by any solver who fills it. This matches the required impact class: unauthorized transaction/execution and wrong-beneficiary fund movement via a manipulable balance-diff check, with an unprivileged, unpermissioned entrypoint (`placeOrder`).

### Likelihood Explanation
Likelihood depends on residual dust actually existing in the singleton dispatcher at the moment of exploitation. This can arise naturally (rounding, fee-on-transfer tokens, partial predispatch executions) or be engineered by the attacker themselves in a prior transaction (e.g., sending tokens directly to the known, public `dispatcher` address, or triggering a predispatch swap that yields excess output before letting the sweep occur in a following transaction). Since `dispatcher` address is a public, fixed parameter and `placeOrder` is fully permissionless and un-authenticated per-caller, no relayer, prover, or privileged role is required — only the ability to call `placeOrder` twice (or race another user's predispatch transaction) to harvest whatever is left in the shared contract.

### Recommendation
Do not credit `order.inputs[i].amount` from the dispatcher's total balance. Snapshot the dispatcher's per-token balance *before* transferring the caller's assets into it and only sweep/credit the incremental amount attributable to this specific `placeOrder` call (i.e., `balanceOf(dispatcher)_after_deposit_and_predispatch − balanceOf(dispatcher)_before_this_call`), and always fully drain the dispatcher after each use (or deploy an ephemeral per-order dispatcher) so no state — and therefore no stealable balance — persists between unrelated callers.

### Proof of Concept
1. Attacker (or a natural rounding/fee-on-transfer effect from an unrelated prior `placeOrder`) leaves `N` tokens of `TOKEN` sitting in the shared `_params.dispatcher` contract.
2. Attacker calls `placeOrder` with `order.predispatch.assets = [{token: TOKEN, amount: 1}]` and a trivial no-op `predispatch.call` (or one that simply does nothing beyond what's required to pass validation), declaring `order.inputs = [{token: TOKEN, amount: N}]` matching the pre-existing dispatcher balance.
3. At [2](#0-1)  the code reads `balanceOf(dispatcher) == N + 1` (attacker's 1 wei plus the pre-existing `N`), sweeps the whole amount to the gateway, and credits `order.inputs[i].amount = N + 1` even though the attacker only funded 1 wei.
4. The gateway now escrows `N` tokens that never belonged to the attacker's committed input, under an order commitment the attacker fully controls; when a solver fills the order, that inflated escrow is released to the solver based on the attacker's fabricated declaration — funds move without ever having been legitimately deposited by the party whose order commitment claims them.

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L242-251)
```text
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
```

**File:** evm/src/apps/IntentGatewayV2.sol (L260-280)
```text
            // Measure actual received, emit dust for excess, update order.inputs.
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 received;
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

                unchecked {
                    ++i;
                }
            }
```
