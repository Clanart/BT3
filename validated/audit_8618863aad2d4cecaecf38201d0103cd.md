## Analog Identified: Predispatch Sweep Uses Shared-Contract `balanceOf()` Delta Instead of Per-Caller Accounting

### Title
Cross-order fund theft via shared `CallDispatcher` balance sweep in `IntentGatewayV2.placeOrder` — ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
The external report's core defect is: computing a value ("exchange rate"/"received amount") from a contract's **raw, shared `balanceOf()`** instead of an **internal per-owner ledger**, letting one actor's deposit/withdrawal sequence corrupt what a later actor is credited for. `IntentGatewayV2.placeOrder`'s predispatch path reproduces this exact anti-pattern: it sweeps the **entire current balance** of the shared `CallDispatcher` contract into escrow for whichever order happens to list that token in `order.inputs`, rather than tracking what that specific caller's predispatch call actually produced.

### Finding Description
In the predispatch branch of `placeOrder`, tokens are pushed to a single, persistent, shared `dispatcher` contract (`_params.dispatcher`, set once for the whole gateway) and arbitrary attacker-supplied calldata (`order.predispatch.call`) is executed against it via `ICallDispatcher(dispatcher).dispatch(...)`: [1](#0-0) 

The docstring explicitly endorses using this for multi-hop conversions such as "unwrapping LP tokens" — i.e., a caller's predispatch call is expected to legitimately transform one input token into one or more *different* output tokens on the shared `dispatcher`: [2](#0-1) 

After the arbitrary call executes, the gateway sweeps tokens out of `dispatcher` **only for the token addresses listed in the current caller's `order.inputs`**, and for each it reads and sweeps the dispatcher's **full current balance**, not an amount tied to this caller: [3](#0-2) 

The "received" amount actually credited to escrow is then derived purely from a `balanceOf()` delta on `address(this)`, and only the portion exceeding the caller's *declared* amount is discarded as "dust" (an event only — never returned to anyone): [4](#0-3) 

Because a legitimate predispatch conversion can produce a token **not included** in that user's own `order.inputs` list (e.g., a multi-output DEX swap or LP unwrap that yields token B and token C, while the order only lists token B), token C is left stranded on the shared `dispatcher` after that transaction completes successfully — no code path returns it to the depositor or credits it to their escrow.

A second, unrelated caller can then submit a new `placeOrder` whose `order.inputs` includes that stray token C with an `amount` set to exactly the stranded balance, and whose own `predispatch.assets` does **not** need to include token C at all (they fund nothing for it). The sweep step reads `balanceOf(dispatcher)` — which is the first victim's stranded balance — passes the `balance < requiredAmount` check, and sweeps the **whole** balance into the gateway. Since `received == order.inputs[i].amount` exactly, it falls into the credit branch and is escrowed as if the attacker had deposited it themselves: [5](#0-4) 

The attacker now controls an order with real escrowed value, funded entirely by another user's stranded predispatch output, and can cancel it (same-chain cancel is user-initiated and unconditioned on the token's provenance) or have a colluding solver fill it to extract the value.

### Impact Explanation
This is unauthorized fund extraction from the gateway's pooled balance: an unprivileged caller obtains escrow credit — and ultimately transferable/withdrawable value — for tokens they never deposited, sourced from another user's legitimate but incompletely-swept predispatch conversion. It matches "stealing or loss of funds" / "transaction manipulation" under the bounty scope, using only the public `placeOrder` entrypoint with no relayer, prover, or admin involvement.

### Likelihood Explanation
Requires only: (1) any legitimate user submitting a predispatch order whose call produces an output token not listed in their own `order.inputs` (a normal, docs-endorsed usage pattern for multi-hop/LP-unwrap flows), and (2) an attacker watching mempool/chain state and submitting a follow-up `placeOrder` referencing the stranded token before it is swept by anything else. No special privileges, timing races beyond normal front-running of on-chain state, or malicious relayer/prover assumptions are needed.

### Recommendation
Replace the shared-contract `balanceOf()` delta pattern with **per-call, per-caller accounting**: have `CallDispatcher` return exactly the amounts routed by the executed calldata (or use a fresh, single-use dispatcher/clone per `placeOrder` call), and sweep only the amount attributable to the current transaction rather than the dispatcher's total current balance. Any genuine leftover dust should be swept to a governance-controlled treasury immediately within the same transaction, never left available for a subsequent, unrelated caller to claim through an unrelated order's input list.

### Proof of Concept
1. User A calls `placeOrder` with `predispatch.call` that swaps input token X into tokens B and C on `dispatcher`, but `order.inputs` only lists token B (as intended for their trade). Token C, worth real value, remains on `dispatcher` after A's transaction completes successfully.
2. Attacker calls `placeOrder` with `order.inputs = [{token: C, amount: <dispatcher's balance of C>}]`, `predispatch.assets` empty of C, and any no-op/self-serving `predispatch.call`.
3. In the sweep loop, `balanceOf(dispatcher)` for token C equals A's stranded amount, passes the `balance >= requiredAmount` check, and the full balance is transferred to the gateway and credited 1:1 to the attacker's order (`received == amount`, credit branch).
4. Attacker cancels the order (same-chain, user-initiated) or arranges a colluding fill, extracting token C's value with zero deposit of their own.

I was not able to inspect `evm/src/utils/CallDispatcher.sol` in full within this session to confirm whether `dispatch()` itself imposes any balance-isolation or single-use guard that might mitigate step 1's precondition — this should be verified directly in a Devin session with full file access before treating the finding as fully confirmed.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L151-157)
```text
     *    hash is computed over the fee-reduced inputs so solvers only need to match
     *    the post-fee amounts.
     * 3. If the order includes predispatch calldata, executes it via the CallDispatcher
     *    (e.g., unwrapping LP tokens) before escrowing the resulting balances.
     * 4. Otherwise, transfers input tokens directly from the caller into escrow.
     * 5. If the order includes solver fees, collects them in the protocol
     *    fee token — swapping from native token via Uniswap V2 if necessary.
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
