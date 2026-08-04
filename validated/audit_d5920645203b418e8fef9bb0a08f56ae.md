### Title
Full-token-balance escrow grab on per-output full-fill drains escrow reserved for other output pairs of the same order - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`_fillSameChain()` releases escrow per output/input index pair. When a given output index reaches its full required amount, the function does not compute the proportional escrow release for that index — it instead reads and releases **the entire current escrow balance for that token address** (`_orders[commitment][token]`). If an order's `inputs`/`output.assets` arrays contain more than one pair that use the *same* input token (a legitimate array layout the contract never rejects), completing one pair early releases escrow that is still owed to a different, not-yet-filled pair in the same order.

### Finding Description
In `_fillSameChain`: [1](#0-0) 

```
if (totalRequired > amountFilled) isFullyFilled = false;
if (protocolShare > 0) emit DustCollected(token, protocolShare);

uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
```

For a partial fill (`amountFilled != totalRequired`) the release is correctly proportional: `order.inputs[i].amount * fillAmount / totalRequired`. But the moment a *single output index* reaches its own `totalRequired` (not necessarily the whole order — `isFullyFilled` is only true once *every* index is done), the code instead pulls `_orders[commitment][token]`, i.e. **the entire remaining escrow balance stored under that token key for the whole commitment**, and schedules it for release via `_withdraw`: [2](#0-1) 

```
function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
    ...
    uint256 escrowed = _orders[body.commitment][token];
    if (escrowed == 0) revert UnknownOrder();
    _orders[body.commitment][token] = escrowed - amount;
    ...
}
```

`_orders` is keyed only by `(commitment, token address)`, not by output index: [3](#0-2) 

Nothing in `_fillSameChain` or the `Order`/`FillOptions` structures prevents an order from listing the same input token against two different output pairs at different indices (e.g. `output.assets[0]` = 50 DAI, `output.assets[1]` = 50 USDT, with `inputs[0].token == inputs[1].token == USDC`). When the solver fully satisfies `output.assets[0]` in one `fillOrder` call (or across several partial fills that happen to complete index 0 first), line 118 grabs *all* USDC still escrowed for the commitment — including the amount reserved to pay for index 1's fill — and schedules it in `escrowedInputs[0]`.

This has two consequences:
1. The filler of the first pair is over-paid with escrow that belonged to the second, unfilled pair (loss of funds for the order's user / for whichever solver later attempts to complete the second pair).
2. When a subsequent solver later tries to complete `output.assets[1]`, `_withdraw` is invoked with the (now correctly, proportionally computed) `escrowedAmount` for index 1, but `_orders[commitment][token]` has already been zeroed by the first release — `_withdraw` reverts with `UnknownOrder()`. The order is now permanently stuck: it can never be fully filled (the reverting index blocks `isFullyFilled` from being reached), and `_cancelSameChain` will also see `escrowed == 0` for that token and, if all remaining tokens are similarly drained, revert with `UnknownOrder()` as well: [4](#0-3) 

This mirrors the DittoETH bug's core pattern exactly: a fill-progress state transition (`_partialFills[commitment][outputToken]` reaching `totalRequired` for one sub-component) triggers a shortcut ("just grab everything left") that does not respect the invariant needed for the *other* sub-components of the same aggregate record to later be exited/settled — the follow-up completion action (`fillOrder` on the remaining pair, or `cancelOrder`) then reverts because the resource it needs (`_orders[commitment][token]`) was already consumed.

### Impact Explanation
This is fund loss/misdirection plus a fund-lock DoS scoped to the impact gate: escrow legitimately reserved for one output/input pair of a same-chain order is redirected to the solver of a different pair, and the order can become permanently unfillable and uncancellable, locking the user's remaining input tokens for that pair. No malicious relayer, prover, or admin is required — this is triggerable purely by two independent solvers (or one solver in two calls) racing to fill different pairs of an ordinary, honestly-placed order that happens to reuse a token across index positions.

### Likelihood Explanation
Likelihood is moderate: it requires an order whose `inputs`/`output.assets` arrays repeat the same input token at two different indices, which is not rejected anywhere in the placement or fill paths reviewed. Multi-asset same-chain orders with repeated stablecoin inputs (e.g., splitting a large USDC input across two different desired outputs) are a plausible real usage pattern, and no special privileges are needed to trigger the full-balance grab once such an order exists — any solver filling one pair first is enough.

### Recommendation
Track escrow release per `(commitment, index)` instead of per `(commitment, token)`, or otherwise scope the "release remaining balance on full fill" shortcut (line 118) to the amount originally escrowed for that specific index rather than the aggregate `_orders[commitment][token]` balance. Reject orders at `placeOrder` time that reuse the same input token across multiple index positions, or maintain a separate per-index escrow accounting structure so that completing one output pair cannot consume escrow belonging to another pair of the same order.

### Proof of Concept
1. User places a same-chain order with `inputs = [ {USDC, 100}, {USDC, 50} ]` and `output.assets = [ {DAI, 100}, {USDT, 50} ]` (both inputs use the same USDC token, at indices 0 and 1). At placement, `_orders[commitment][USDC]` accumulates to `150` (both input entries share the same map key).
2. Solver A calls `fillOrder` supplying `outputs[0] = 100 DAI` and `outputs[1] = 0`. In the loop, index 0 fully completes (`amountFilled == totalRequired`), so `escrowedAmount = _orders[commitment][USDC] = 150` is scheduled for release to Solver A via `_withdraw`, even though only 100 USDC was owed for index 0. `_orders[commitment][USDC]` is now `0`.
3. Solver B later calls `fillOrder` supplying `outputs[1] = 50 USDT`. Index 1 also fully completes, so `escrowedAmount = _orders[commitment][USDC] = 0` (already drained) is passed to `_withdraw`, which hits `if (escrowed == 0) revert UnknownOrder();` — the fill reverts.
4. The order can never reach `isFullyFilled`, and `_cancelSameChain` will likewise find `_orders[commitment][USDC] == 0` for the remaining input, so the user cannot cancel or recover the 50 USDC that was meant to back output index 1 — Solver A received 50 USDC more than entitled, and the user's remaining escrow position is stuck.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L113-123)
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
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-187)
```text
        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-141)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

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
