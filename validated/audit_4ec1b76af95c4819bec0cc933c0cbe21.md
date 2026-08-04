# Escrow over-release when an order's inputs array repeats a token across multiple output legs - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`IntentGatewayV2.placeOrder()` only rejects **duplicate output tokens** — it never checks for duplicate **input** tokens. Escrow, however, is tracked as a single aggregate bucket per `(commitment, token)` in `_orders`, not per input index. `_fillSameChain()`'s partial-fill accounting releases the **entire remaining bucket** for an input token the moment the *single output leg it happens to be paired with* is completed, even if the same input token also backs a second, still-unfilled output leg. A solver can therefore fully satisfy the cheap leg of a multi-leg order and walk away with the escrow that was meant to also cover the other, unfilled leg.

### Finding Description
`placeOrder` (evm/src/apps/IntentGatewayV2.sol:162-189) explicitly dedups `order.output.assets[i].token` via transient-storage `tload`/`tstore`, reverting with `InvalidInput()` on a repeat, but performs **no equivalent check on `order.inputs`**. [1](#0-0) 

`_orders` is documented and implemented as a single value keyed by `(commitment, token)`: [2](#0-1) 

So if `order.inputs` contains the same token at two indices (paired 1:1 positionally with two different output legs, enforced only by `order.inputs.length == order.output.assets.length` at fill time), both escrow transfers land in the same bucket.

In `_fillSameChain`, the escrow amount to release for a leg is computed as:
```solidity
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
``` [3](#0-2) 

When a leg finishes (`amountFilled == totalRequired`), the code sweeps the **entire live `_orders[commitment][token]` balance** for that token — not just the portion attributable to that leg — to avoid leaving rounding dust behind. This is only safe if the token is unique to that single leg. If a second, still-open leg shares the same input token, its backing collateral is swept away as "residual dust" of the first leg.

`_withdraw` then unconditionally executes the token transfer regardless of `finalize`:
```solidity
function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
    ...
    for (uint256 i; i < len; i++) {
        ...
        uint256 escrowed = _orders[body.commitment][token];
        if (escrowed == 0) revert UnknownOrder();
        _orders[body.commitment][token] = escrowed - amount;
        ... transfer amount to beneficiary ...
    }
    if (finalize) { ... }
}
``` [4](#0-3) 

The transfer loop runs even when `finalize == false` (i.e., the order is left open as a partial fill — `_fillSameChain` even `delete _filled[commitment]` in that branch), so the drained-but-still-open order can never be topped back up. [5](#0-4) 

### Impact Explanation
Any unprivileged solver calling the public `fillOrder` entrypoint can:
1. Fully satisfy only the cheapest output leg of a multi-leg order that shares an input token across legs.
2. Trigger the "leg complete" branch, which sweeps the **entire remaining escrow bucket** for that shared input token — including the portion that was meant to collateralize the other, unfilled leg.
3. Leave the order in a permanently under-collateralized, still-open state: the other leg's fill will find `_orders[commitment][token] == 0` and either revert (`UnknownOrder`) or silently release zero tokens (amount `== 0` is skipped in `_withdraw`), and the user's same-chain cancellation path (`_cancelSameChain`) will find `hasEscrow == false` and revert, permanently locking/losing the user's remaining funds.

This is a direct loss-of-funds / wrong-beneficiary-amount bug reachable by any solver with no need for a malicious relayer, prover, or governance actor, and it is not a front-running/slippage scenario — it is a structural escrow-accounting flaw in the intent settlement logic.

### Likelihood Explanation
The only precondition is that the order creator (or a solver colluding with/being the order creator, or simply an order that legitimately reuses one input token to fund two output legs — e.g. splitting one asset to buy two different outputs) places an order whose `inputs` array repeats a token address across two indices. Nothing in `placeOrder` prevents this, unlike the explicit anti-duplicate check applied to outputs. The fill path is the standard public `fillOrder` call; no proof forgery, no consensus/relayer trust assumptions, and no admin privileges are required.

### Recommendation
- Mirror the output-token dedup check in `placeOrder` for `order.inputs`, rejecting orders with a repeated input token address, or
- Track escrow per `(commitment, inputIndex)` instead of per `(commitment, token)`, or
- In `_fillSameChain`, compute the "full-fill" escrow release as the exact pro-rata amount for that specific input entry (`order.inputs[i].amount`) rather than the live aggregate `_orders[commitment][token]` balance, reconciling any rounding dust separately and only after all paired legs are fully filled.

### Proof of Concept
1. User places an order with:
   - `inputs = [{token: USDC, amount: 100}, {token: USDC, amount: 50}]`
   - `outputs = [{token: DAI, amount: 100}, {token: WETH, amount: 50}]`
   - `placeOrder` escrows both amounts into the single bucket `_orders[commitment][USDC] = 150`; the output-token dedup check passes since DAI ≠ WETH.
2. A solver calls `fillOrder` supplying `options.outputs = [{DAI, 100}, {WETH, 0}]`.
   - For index 0 (DAI leg): `totalRequired = 100`, `solverAmount = 100` → `fillAmount = 100`, `amountFilled == totalRequired` → `escrowedAmount = _orders[commitment][USDC] = 150` (the full bucket, not the 100 attributable to this leg).
   - For index 1 (WETH leg): `solverAmount = 0`, `remaining = 50 > 0` → `isFullyFilled = false`; leg skipped.
3. `_withdraw` transfers the full 150 USDC to the solver even though `isFullyFilled = false` (transfer loop runs unconditionally); `_filled[commitment]` is deleted, leaving the order "open."
4. `_orders[commitment][USDC]` is now `0`. The WETH leg can never be properly collateralized: a later filler completing it gets `escrowedAmount = 0` (no input reward), and the user's `_cancelSameChain` call reverts with `UnknownOrder` because `hasEscrow` is false — the 50 USDC meant to back the WETH leg is unrecoverable, having been paid out entirely against the DAI leg.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L163-189)
```text
        if (order.inputs.length == 0) revert InvalidInput();

        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
            }
            unchecked {
                ++i;
            }
        }
```

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L126-142)
```text
        // Orders carrying output calldata must be filled completely in a single fill.
        // The attached call is only executed on a full fill, so a partial fill would
        // leave the intended side effect unexecuted while releasing proportional escrow.
        if (order.output.call.length > 0 && !isFullyFilled) revert PartialFillNotAllowed();

        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);

        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        }
```
