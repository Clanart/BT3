### Title
Escrow release keyed by token address (not by output leg) lets a solver drain shared-token escrow via a partial same-chain fill — ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
The Intent Gateway's same-chain fill path (`_fillSameChain`) computes the escrow amount to release for a fully-completed output leg by reading the *entire* current balance of `_orders[commitment][token]` rather than the amount specifically backing that leg. Because `_orders` is keyed only by `(commitment, token address)` and aggregates every input entry that shares a token — not per output leg — a solver can fully complete one output leg of a multi-leg order while leaving other legs untouched, and walk away with the whole pooled escrow for that token, including the portion meant to back the still-unfilled legs. This is the same "insufficient handling of partial completion" class as the PufferVault report: partial progress in a multi-step operation is not tracked/reconciled per-unit, so the accounting silently over-releases and the remaining portion of the operation becomes permanently unfulfillable (funds effectively lost/frozen for the unfilled leg, over-extracted for the filled one).

### Finding Description
`_orders` is documented and implemented as a per-token, per-commitment aggregate balance: [1](#0-0) 

In `_fillSameChain`, a same-chain order can have multiple `(input, output)` leg pairs correlated by array index `i`. Each leg tracks its own fill progress in `_partialFills[commitment][outputToken]`, but the amount actually transferred out of escrow for a *fully completed* leg is computed as the raw current balance of `_orders[commitment][inputToken]`, not the leg's proportional share: [2](#0-1) 

A single `fillOrder` call can supply a non-zero `solverAmount` for one leg (driving it to full completion) while supplying `0` for another leg of the same order (which is simply skipped via the early `continue`): [3](#0-2) 

If two legs of the order escrow the same input token (a natural pattern for an order that funds multiple outputs from one input asset), the leg that reaches `amountFilled == totalRequired` first reads and drains the *whole* `_orders[commitment][token]` bucket — including the share that should remain reserved for the other, still partially/unfilled leg. `_withdraw` then unconditionally transfers that full amount out and decrements the shared balance: [4](#0-3) 

Because the order is not finalized (`isFullyFilled` is `false` due to the skipped leg), `_filled[commitment]` is deleted so the order stays open for further fills, but the escrow that was supposed to back the remaining leg's proportional release has already been paid out: [5](#0-4) 

The remaining leg can never be completed correctly — either its later fill will fail (`escrowed == 0` → `revert UnknownOrder()` in `_withdraw`) or, if any residual balance happens to still exist, it will compute an incorrect proportional amount against an already-drained pool. Either way the accounting invariant "escrow for a token must be released to the party actually entitled to it, exactly once and only in the entitled amount" is broken.

### Impact Explanation
This directly matches the bounty's "stealing or loss of funds" and "logic attacks" categories:
- A solver can extract more escrowed value than the output tokens they actually delivered, by fully completing only the cheaper/target leg of a multi-leg, same-token order and skipping the rest.
- The order's user is left with a permanently stuck, non-cancellable-in-full state for the remaining leg — since `_cancelSameChain` refunds whatever remains in `_orders[commitment][token]`, which will be zero or reduced by the amount stolen for the other leg, so the user cannot recover the value that should have backed the unfilled leg. This is exactly the "permanent freezing/loss of funds" impact called out in the source report.
- The entrypoint (`fillOrder` → `_fillSameChain`) is fully public/unprivileged; no relayer, prover, admin, or malicious peer is required — an ordinary solver can trigger this with a single transaction.

### Likelihood Explanation
Any order with two or more output legs whose corresponding input entries share the same token address (a common and intentional design for orders that fund multiple outputs from a single input asset) is exploitable. No special permissions, front-running, or off-chain cooperation are needed — a solver simply crafts `FillOptions.outputs[]` with a non-zero amount for one leg and zero for the other(s) in a normal `fillOrder` call.

### Recommendation
Track escrow per `(commitment, output-leg index)` or per `(commitment, input-token, leg)` rather than aggregating purely by `(commitment, token)`. Alternatively, when computing the "full remaining balance" release path (the `amountFilled == totalRequired` branch), cap the amount actually withdrawn to the leg's own proportional share of the *original* input amount rather than reading the live aggregate `_orders[commitment][token]` balance, and only sweep any true residual dust once every leg sharing that token has reached full completion.

### Proof of Concept
1. User places a same-chain order with two output legs both funded from the same input token `USDC`:
   - Leg 0: input `500 USDC` → output `500 DAI`
   - Leg 1: input `500 USDC` → output `0.5 WETH`
   - `_orders[commitment][USDC] = 1000` after `placeOrder` escrows both legs' USDC together (aggregated by token per `IntentsBase.sol:140`).
2. Solver calls `fillOrder(order, FillOptions{ outputs: [ {DAI, 500}, {WETH, 0} ] })`.
3. In `_fillSameChain`, leg 0 (`i=0`): `solverAmount=500=totalRequired`, `amountFilled==totalRequired` → `escrowedAmount = _orders[commitment][USDC] = 1000` (the *entire* bucket, not just leg 0's 500) per `IntrinsicIntents.sol:116-122`.
4. Leg 1 (`i=1`): `solverAmount=0` → `continue`, `isFullyFilled=false`.
5. `_withdraw` transfers `1000 USDC` to the solver and sets `_orders[commitment][USDC] = 0` (`IntentsBase.sol:403`), while only delivering `500 DAI` worth of value to the beneficiary.
6. Order remains open (`_filled[commitment]` deleted), but leg 1 can never be filled/cancelled correctly: any later fill attempt or cancellation finds `_orders[commitment][USDC] == 0`, reverting with `UnknownOrder()` or refunding nothing — the 500 USDC meant for leg 1 is gone, captured entirely by the solver in step 3-5.

I could not directly view the `placeOrder`/escrow-accumulation function in this session to confirm the exact aggregation code path at order-placement time, but the storage model documented in `IntentsBase.sol:136-140` ("Maps (commitment, token address) to the escrowed amount for that token") and the release logic in `IntrinsicIntents.sol:116-122` together are sufficient to establish that escrow is not partitioned per output leg, which is the root cause of this issue.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-410)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L74-79)
```text
            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L113-124)
```text
            if (totalRequired > amountFilled) isFullyFilled = false;
            if (protocolShare > 0) emit DustCollected(token, protocolShare);

            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L136-142)
```text
        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        }
```
