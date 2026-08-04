## Finding: Duplicate-token escrow double-crediting in Tron `IntentGatewayV2.placeOrder`

### Title
Missing duplicate-input-token guard lets `_orders[commitment][token]` be double-credited against a single balance snapshot in the predispatch escrow path - (File: `evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The wXTZ bug's core defect is: a per-account balance is credited by reading and adding to a pre-mutation value without the update being atomic/exclusive per key, so the same value gets folded in twice. `IntentGatewayV2.placeOrder` in the Tron variant of the IntentGateway contract has the same structural weakness in its escrow-crediting loop: it accumulates into `_orders[commitment][token]` with `+=` from a *balance snapshot taken before any transfer executes*, and — unlike the canonical `evm/src/apps/IntentGatewayV2.sol` — it never rejects duplicate token entries in `order.inputs`.

### Finding Description
In the canonical EVM `IntentGatewayV2.sol`, escrow crediting explicitly guards against duplicate input tokens: [1](#0-0) 

That check is `if (_orders[commitment][token] != 0) revert InvalidInput();` before a plain assignment `_orders[commitment][token] = reducedInputs[i].amount;`.

The Tron fork of the same contract removes this guard entirely and instead uses accumulation (`+=`) in both escrow branches: [2](#0-1) [3](#0-2) 

In the predispatch (dispatcher) branch, the "record balance" loop (lines 412-440) queries `IERC20(token).balanceOf(dispatcher)` for *each* `order.inputs[i]` entry **before any of the transfer calls are dispatched** — the transfer calls are only executed afterward, batched together via `ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls))`. If `order.inputs` contains the same `token` address in two (or more) entries, every iteration reads the identical, un-decremented dispatcher balance `B`, and each iteration unconditionally executes:
```
_orders[commitment][token] += reducedInputs[i].amount;
```
This credits the same underlying balance to escrow multiple times, exactly mirroring the wXTZ pattern where `tokenStorage.ledger` (a stale pre-update value) is read and folded into `newTokens` twice.

Because the raw `Call` in `transferCalls[i]` uses a bare low-level `.call()` with `IERC20.transfer.selector` (not `SafeERC20.safeTransfer`), the boolean return value of the second, now-insufficient-balance transfer is never checked at this call site — whether the overall `dispatch()` reverts on that failed transfer depends entirely on `ICallDispatcher`'s semantics (revert-propagating vs. best-effort per-call). Because the increments to `_orders[commitment][token]` happen in the loop **prior to and independent of** the dispatch call, any dispatcher implementation that does not hard-revert the whole batch on an individual call failure (a legitimate multicall-style pattern) leaves the doubled escrow value committed to storage even though only one balance's worth of tokens actually landed in the gateway.

### Impact Explanation
`_orders[commitment][token]` is the on-chain escrow ledger consulted by `fillOrder` and by order-cancellation/refund logic to determine how much a solver may claim or how much a canceling user should receive back. Inflating this value beyond what the contract actually holds for that commitment allows the order's designated beneficiary (the attacker, as the order creator) to claim/settle an amount the gateway never actually received for this order — the shortfall is paid out of the shared token pool that also backs other users' unrelated escrowed orders, i.e., direct loss of other users' funds / fund lock for legitimate solvers and depositors. This is a false-state-acceptance / unauthorized-amount class bug matching the bounty's "stealing or loss of funds" and "logic attacks" categories, reachable by any unprivileged caller of the public `placeOrder` entrypoint with a crafted `order.inputs` array — no relayer, prover, or admin involvement required.

### Likelihood Explanation
Medium-high, contingent on `ICallDispatcher`'s call-execution semantics (silent per-call failure vs. atomic revert), which was not available to fully confirm within the scanned code excerpts. What is confirmed with certainty from local code is the structural defect: the duplicate-token rejection present in the sibling canonical contract is absent here, and the escrow accumulation is computed from a balance read that is not re-validated after the batched transfer executes. This is exactly the missing-invariant class the external report calls out ("make sure all invariants are satisfied after every message").

### Recommendation
**Short term:** Add the same duplicate-input-token guard used in `evm/src/apps/IntentGatewayV2.sol` (`if (_orders[commitment][token] != 0) revert InvalidInput();`) to the Tron `placeOrder`, and/or replace the pre-dispatch balance snapshot with a genuine before/after balance delta measured *after* `dispatch()` returns (the same pattern already used in `evm/src/apps/IntentGatewayV2.sol`'s fee-on-transfer handling, which snapshots `balancesBefore` and computes `received = balance_after - balance_before` per iteration — see [4](#0-3) ). Ensure every individual transfer inside `ICallDispatcher.dispatch` reverts the whole batch on failure (or checks return booleans) rather than silently continuing.

**Long term:** Add differential/fuzz tests across both `IntentGatewayV2.sol` copies (canonical and Tron) asserting that `sum(_orders[commitment][*])` never exceeds actual gateway token balances for any input containing duplicate token entries, mirroring the "two addresses representing the same account" edge-case testing recommended in the external report.

### Proof of Concept
1. Attacker calls `placeOrder` with:
   - `order.predispatch.call` / `order.predispatch.assets` crafted so that after the predispatch call executes, the `dispatcher` contract holds balance `B` of token `T`.
   - `order.inputs = [ {token: T, amount: B}, {token: T, amount: B} ]` (same token twice).
2. In the "record balance" loop, both iterations read `balance = IERC20(T).balanceOf(dispatcher) == B` (transfers not yet executed), each passes the `balance < requiredAmount` check, and each executes `_orders[commitment][T] += reducedInputs[i].amount`, crediting escrow `2×reducedAmount(B)`.
3. `ICallDispatcher(dispatcher).dispatch(abi.encode(transferCalls))` executes both queued transfers; only the first moves real tokens (`B`) into the gateway. If the second (now-underfunded) transfer does not hard-revert the batch, the transaction completes with `_orders[commitment][T]` showing double the tokens actually escrowed.
4. On settlement/cancellation, the beneficiary can claim the inflated `_orders[commitment][T]` value, draining the excess from the gateway's shared token balance that backs other unrelated orders.

Confidence caveat: step 3's outcome depends on `ICallDispatcher`'s revert-on-failure behavior, which was not located/confirmed in the scanned excerpts of this repository; this should be verified directly against the `ICallDispatcher` implementation before treating the PoC as fully deterministic.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L229-280)
```text
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

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L407-440)
```text
            // Execute the call dispatcher with predispatch call
            ICallDispatcher(dispatcher).dispatch(order.predispatch.call);

            // Transfer tokens from call dispatcher back to IntentGateway
            Call[] memory transferCalls = new Call[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                address token = address(uint160(uint256(order.inputs[i].token)));
                uint256 requiredAmount = order.inputs[i].amount;
                uint256 balance;

                if (token == address(0)) {
                    balance = address(dispatcher).balance;
                    if (balance < requiredAmount) revert InsufficientNativeToken();
                    transferCalls[i] = Call({to: address(this), value: balance, data: ""});
                } else {
                    balance = IERC20(token).balanceOf(dispatcher);
                    if (balance < requiredAmount) revert InvalidInput();
                    transferCalls[i] = Call({
                        to: token,
                        value: 0,
                        data: abi.encodeWithSelector(IERC20.transfer.selector, address(this), balance)
                    });
                }

                uint256 dust = balance - requiredAmount;
                if (dust > 0) emit DustCollected(token, dust);

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-463)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
        }
```
