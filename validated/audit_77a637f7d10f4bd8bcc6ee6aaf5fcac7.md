### Title
`_fillSameChain()` releases the entire pooled per-token escrow instead of a leg's allocated share, allowing one output leg to drain another leg's escrow - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`IntentsBase._orders` is a `mapping(bytes32 commitment => mapping(address token => uint256))` — an escrow ledger keyed **only by token address**, with no per-leg/per-index dimension [1](#0-0) . `_fillSameChain()` iterates `order.output.assets` by index `i` and, on the leg that reaches `amountFilled == totalRequired`, releases escrow by reading the **whole current balance** of `_orders[commitment][token]` rather than the amount actually allocated to that specific input/output leg pair: [2](#0-1) 

This is structurally the same broken invariant as H-09: a value is taken from a shared pool ("available supply") without checking or capping it against the specific allocation it corresponds to, so the entitlement of other legs can be stolen or destroyed.

### Finding Description
`_orders[commitment][token]` is a single running balance per `(commitment, token)` pair. Because `order.inputs` is a caller-supplied array of `TokenInfo{token, amount}` with no uniqueness constraint enforced anywhere in `IntentsBase`/`IntrinsicIntents`, an order can legitimately contain two (or more) input legs that use the **same token address** but correspond to two different output legs (`order.output.assets[i]`), each with its own `totalRequired` and `_partialFills[commitment][outputToken]` tracking.

In `_fillSameChain`, when leg `i` is completed in a single call (`amountFilled == totalRequired`), the code does not compute "this leg's proportional share of the pooled token balance" — it takes the pool's *entire remaining balance*:
```solidity
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
}
``` [3](#0-2) 

`_withdraw()` then subtracts exactly that amount from the shared pool and transfers it to the solver who completed leg `i`: [4](#0-3) 

If `order.inputs[i].token == order.inputs[j].token` for `i != j`, whichever leg's `totalRequired` is satisfied first captures the pool balance that was meant to be split between legs `i` and `j`. The solver completing leg `i` receives leg `j`'s rightful escrow as well, and when leg `j` is subsequently filled, `_orders[commitment][token]` is now short: either the proportional read at line 120 (`(order.inputs[j].amount * fillAmount) / totalRequired`) still returns a nonzero amount that `_withdraw` cannot satisfy (drains below zero via `escrowed - amount` underflow revert since Solidity ≥0.8 reverts on underflow, `UnknownOrder`/revert — locking leg j's solver out and leaving user's order stuck), or, in a partial ordering where leg `j` reaches completion first for a different fraction, funds intended for leg `i` are similarly siphoned.

Existing guards do not stop this because:
- There is no check that `order.inputs[i].token` values are unique across the `inputs` array.
- The escrow read for a "fully filled" leg is unconditional (`_orders[commitment][token]`), not derived from `order.inputs[i].amount` or any per-leg cap.
- `_withdraw`'s only guard is `escrowed == 0 → revert UnknownOrder`, which does not prevent taking more than the leg's own allocation while `escrowed > 0`.

### Impact Explanation
This falls squarely under the bounty's "logic attacks / unauthorized transaction / wrong beneficiary or amount / fund loss" categories: a same-chain intent order with repeated input tokens across legs lets whichever solver races to complete one leg first receive escrow belonging to another leg/solver, and can permanently lock the remainder of the order (subsequent leg fills revert or under-release), causing loss of user funds and/or loss of the second solver's rightful payout — all reachable by ordinary, unprivileged `fillOrder` callers with no relayer/prover/admin compromise required.

### Likelihood Explanation
The order itself (including `order.inputs`) is fully attacker/user-controlled at `placeOrder` time; nothing in `IntentsBase`/`IntrinsicIntents` rejects duplicate token addresses across input legs. Any user constructing a multi-output order that reuses the same input token for two output legs — a plausible real-world case (e.g., paying the same source token toward two different destination assets/beneficbr legs in one order) — triggers the pooled-balance read the moment either leg completes in a single fill. No special timing, governance, or malicious infrastructure is needed.

### Recommendation
Track escrow per leg/index (e.g., `mapping(bytes32 => uint256[]) _orderInputBalances` keyed by the input index, or key `_orders` by `(commitment, inputIndex)` instead of `(commitment, token)`), or explicitly compute the leg's proportional/allocated share (`order.inputs[i].amount` minus what has already been released for that specific index) instead of reading the full pooled `_orders[commitment][token]` balance. Alternatively, reject orders whose `inputs` array contains duplicate token addresses at `placeOrder` time.

### Proof of Concept
1. User places a same-chain order where `order.inputs = [ {token: USDC, amount: 100}, {token: USDC, amount: 50} ]` and `order.output.assets = [ {token: DAI, amount: 100}, {token: WETH, amount: 1} ]` — two legs sharing the same input token (USDC), escrowing `_orders[commitment][USDC] = 150`.
2. Solver A fills output leg 0 (`DAI, 100`) fully in one call. `amountFilled == totalRequired` triggers `escrowedAmount = _orders[commitment][USDC]` = **150** (the pool for both legs), not the 100 USDC allocated to leg 0.
3. `_withdraw` releases the full 150 USDC to Solver A and sets `_orders[commitment][USDC] = 0`.
4. Solver B later fills output leg 1 (`WETH, 1`) fully; the corresponding `escrowedAmount` read/withdraw against `_orders[commitment][USDC]` (now 0) reverts (`UnknownOrder`) or returns 0, so Solver B is filled on the output side but never receives their entitled USDC — funds are lost to Solver A instead of being split correctly between the two legs. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L94-123)
```text
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
