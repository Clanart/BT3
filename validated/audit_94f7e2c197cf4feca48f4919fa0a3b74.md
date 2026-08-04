Confirmed: `_orders` is `mapping(bytes32 commitment => mapping(address token => uint256 amount))` [1](#0-0) , keyed only by **token address**, not by output-leg index. In `_fillSameChain`, the "no-dust" fix reads the *entire remaining escrow bucket* for `order.inputs[i].token` once that output leg's cumulative fill equals its `totalRequired`:

```solidity
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
``` [2](#0-1) 

### Title
Same-token multi-leg orders let a completed output leg drain escrow reserved for a still-unfilled leg - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
The rounding-dust fix for partial fills (paying the "exact remaining balance" instead of the truncated proportional share once a leg is fully filled) reads `_orders[commitment][inputToken]`, which is a **per-token** total, not a **per-output-leg** total. If an `Order` has two or more legs (`order.inputs[i]`, `order.output.assets[i]`) that escrow the *same* input token address, completing the first leg releases the whole remaining token balance for that address — including the portion still owed against the other, unfilled leg.

### Finding Description
`_orders` is declared as `mapping(bytes32 => mapping(address => uint256))` — keyed by `(commitment, tokenAddress)` only [1](#0-0) . When `placeOrder` escrows `order.inputs`, multiple `inputs[i]` entries that share the same `token` accumulate into the same storage slot (this is implicit in the mapping shape; nothing in `IntentsBase` enforces uniqueness of `inputs[].token`).

In `_fillSameChain`, each output leg `i` is processed independently, tracking cumulative fill per **output token** in `_partialFills[commitment][outputToken]` [3](#0-2) . When a leg's `amountFilled` reaches its `totalRequired`, the code assumes "the whole remaining escrow for this input token belongs to this leg" and releases `_orders[commitment][inputToken]` in full via `_withdraw` [2](#0-1) , then decrements the same global bucket in `_withdraw`: `_orders[body.commitment][token] = escrowed - amount;` [4](#0-3) .

If two legs share an input token — e.g., `order.inputs[0].token == order.inputs[1].token == USDC`, escrowing 600 USDC total across two output legs (300 USDC allocated conceptually to each) — a solver who completes leg 0 first will trigger `escrowedAmount = _orders[commitment][USDC]`, which is the **full 600 USDC** still on deposit (since leg 1 hasn't released anything yet), not the 300 USDC actually earmarked for leg 0. The solver receives double what they're owed, and the escrow bucket for the still-open leg 1 is now empty, so leg 1's solver either cannot be paid (later `_withdraw` calls revert with `UnknownOrder` since `escrowed == 0`) or the user who placed the order loses funds that were meant to back leg 1's fill/refund.

Existing guards do not stop this: `PartialFillNotAllowed` only fires when `order.output.call.length > 0` [5](#0-4) , and there is no check anywhere in `IntentsBase` or `IntrinsicIntents`/`ExtrinsicIntents` that rejects orders whose `inputs[]` contain duplicate token addresses across legs.

### Impact Explanation
This is a direct fund-theft/fund-loss primitive reachable by any unprivileged solver: an attacker (acting as solver) can construct or opportunistically fill a multi-leg order that happens to reuse an input token across legs, complete the cheaper/easier leg first, and pull out escrow that was reserved for the other leg. The victim (the user who placed the order, or the second solver/beneficiary) either receives less than promised or the order becomes unpayable, matching the bounty's "stealing or loss of funds" / "wrong beneficiary or amount" categories.

### Likelihood Explanation
Likelihood is moderate: it requires an order with ≥2 output legs whose corresponding input legs use the same token address — a legitimate, unrestricted input shape since `placeOrder`/`Order` struct places no uniqueness constraint on `inputs[].token`. Any user (or a solver colluding with a user, or simply an ordinary user building a multi-leg order without realizing the risk) can create such an order, and any solver filling legs sequentially can trigger the miscalculation without needing any privileged role, malicious relayer, or proof manipulation.

### Recommendation
Track escrow per output leg index rather than by token address alone (e.g., `mapping(bytes32 => uint256[]) _legEscrow` indexed by `i`, or key `_orders` by `(commitment, i)` instead of `(commitment, token)`), or alternatively reject orders at `placeOrder` time whose `inputs[]` contain duplicate token addresses across legs, forcing 1:1 input/output leg correspondence per token.

### Proof of Concept
1. User places an order with `inputs = [{USDC, 300e6}, {USDC, 300e6}]` and `output.assets = [{DAI, 300e18}, {WETH, 1e18}]` (two legs sharing USDC as the input token, escrowing 600 USDC total into `_orders[commitment][USDC] = 600e6`).
2. Solver A fills leg 0 completely by providing exactly `300e18` DAI. In `_fillSameChain`, `amountFilled == totalRequired` for leg 0, so `escrowedAmount = _orders[commitment][USDC]`, which reads `600e6` (the full bucket, since leg 1 hasn't been touched) instead of the `300e6` actually earmarked for leg 0.
3. `_withdraw` transfers all `600e6` USDC to Solver A and sets `_orders[commitment][USDC] = 0`.
4. Solver B attempts to fill leg 1 (WETH) — even though `WETH` fill succeeds and the beneficiary receives WETH, the corresponding escrow release call to `_withdraw` for the USDC input leg finds `_orders[commitment][USDC] == 0` and reverts with `UnknownOrder`, or (depending on ordering) simply pays out nothing further, leaving the user's second leg's backing funds gone with nothing returned.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-403)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L74-98)
```text
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
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-122)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L126-129)
```text
        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();
```
