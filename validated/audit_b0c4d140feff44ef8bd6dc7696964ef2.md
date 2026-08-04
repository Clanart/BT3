## Analysis: Duplicate-token escrow double-release in `IntrinsicIntents._fillSameChain`

### Core reduction of the external bug
The TraderJoe bug's root cause is that a function internally treats two array-indexed values as fungible/positional without validating uniqueness/order, then returns/uses a stale or mismatched value at that index. The Hyperbridge analog is a positional (`order.inputs[i]` / `order.output.assets[i]`) pairing without any check that input tokens are unique, combined with a global-per-token escrow accounting map that is read (not yet decremented) multiple times within the same transaction loop.

### Title
Duplicate-token order inputs cause full escrow amount to be released multiple times in a single `fillOrder` call - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`_fillSameChain` computes the escrow to release for input index `i` using `_orders[commitment][token]` — a mapping keyed by **token address**, not by index — whenever the corresponding output at that same index becomes fully filled: [1](#0-0) . If `order.inputs` contains the same token address at multiple indices (nothing prevents this at order creation), each index independently re-reads the *same, not-yet-decremented* `_orders[commitment][token]` slot and pushes the full stored amount into `escrowedInputs[i]`. All entries are only applied once, in a single batched `_withdraw` call after the loop finishes: [2](#0-1) .

### Finding Description
The escrow-release amount is chosen per-index but sourced from a per-token (not per-index) accounting map, exactly the class of positional/value mismatch seen in the LBRouter bug (index-based logic operating on a shared keyed value without accounting for duplicates/reordering). Nothing in `_fillSameChain` or the earlier order-validation path enforces that `order.inputs[]` token addresses are unique. A user who places an order with the same input token repeated at two indices, matched against two distinct output legs, causes the loop to compute `escrowedAmount = _orders[commitment][token]` (the *entire* remaining escrow for that token) at **both** indices once both corresponding outputs reach `amountFilled == totalRequired`. Since `_orders[commitment][token]` is not decremented inside the loop — only inside `_withdraw`, which is invoked once with the whole `escrowedInputs` array after the loop completes — both array slots carry the *same* full amount, and `_withdraw` is asked to pay out the token twice from a single escrow balance.

### Impact Explanation
This directly produces "stealing or loss of funds" via duplicate settlement: a solver (or a user colluding with themselves as solver in a same-chain fill) can drain more of the escrowed token than was ever deposited, at the expense of the protocol's/user's escrowed balance, or cause `_withdraw` to attempt transferring more than the contract's actual balance for that token (reverting/DoS) depending on `_withdraw`'s internal guard. Either outcome — double payout or broken settlement — falls squarely within the bounty's "double-claim/double-settlement" and "unauthorized transaction/execution" categories.

### Likelihood Explanation
Reachable by any unprivileged user who crafts an `Order` with a duplicated input token address across indices — no relayer, prover, or admin involvement is required, matching the "public-entrypoint, unprivileged attacker" bar. The only precondition is that `_fillSameChain`/`placeOrder` do not reject duplicate token entries in `order.inputs`, which is not visibly enforced in the code shown.

### Recommendation
Track escrow release per (commitment, input-index) rather than per (commitment, token), or enforce uniqueness of token addresses within `order.inputs` at order-placement time, and decrement `_orders[commitment][token]` immediately when computing `escrowedAmount` inside the loop rather than deferring the whole batch to `_withdraw`.

### Proof of Concept
1. User places a same-chain order via `IntentGatewayV2.placeOrder` with `order.inputs = [ {token: X, amount: A}, {token: X, amount: A} ]` (same token `X` twice) and two output legs sized so each can be independently fully filled.
2. Total escrow accounted in `_orders[commitment][X]` after placement is `2A` (or whatever total was actually transferred in).
3. Solver calls `fillOrder` supplying outputs that fully satisfy both output legs in one transaction.
4. In `_fillSameChain`'s loop, at `i=0` (output 0 fully filled): `escrowedAmount = _orders[commitment][X]` reads `2A` and is stored at `escrowedInputs[0]`.
5. At `i=1` (output 1 fully filled): `_orders[commitment][X]` is *still* `2A` (not yet decremented), so `escrowedInputs[1]` is also set to `2A`.
6. `_withdraw` is called once with `escrowedInputs = [2A, 2A]` for the same token `X`, releasing `4A` total against an actual escrow of `2A` — a duplicate/over-release of the escrowed asset. [3](#0-2) [2](#0-1)

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L66-123)
```text
        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
            uint256 fillAmount;

            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }

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
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L131-134)
```text
        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);
```
