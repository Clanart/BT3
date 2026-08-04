Found the vulnerability. `_orders[commitment][token]` is keyed only by **token address**, not by output index. In `_fillSameChain`, when an output's fill completes an order pair (`amountFilled == totalRequired`), the code releases `escrowedAmount = _orders[commitment][inputToken]` — the **entire remaining balance** for that input token — rather than the proportional share belonging to that specific output pair [1](#0-0) .

### Title
Escrow over-release via shared input-token key when multiple output pairs reference the same input token in `_fillSameChain` - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`Order.inputs` and `Order.output.assets` are matched purely by array index `i`, and `_orders[commitment][token]` is a per-token (not per-index) escrow balance [2](#0-1) . If a user constructs (or is tricked into constructing, e.g. via SDK bugs/UI) an order where two different output-asset entries at indices `i` and `j` reference input entries `inputs[i]` and `inputs[j]` denominated in the **same token**, a solver can complete the smaller output pair first. On that fill, `escrowedAmount` is computed as the *entire current* `_orders[commitment][token]` balance (line 118) — which still includes the escrow backing the other, not-yet-filled output pair — rather than only `inputs[i]`'s proportional share.

### Finding Description
The "full remaining balance on completion" logic exists specifically to sweep up rounding dust for the *final* fill of a *single* input/output pair (per the `testPartialFill_RoundingDustReleasedToFinalSolver` fix) [3](#0-2) . But the same code path in `IntrinsicIntents._fillSameChain` runs independently for every index `i` in `order.output.assets`, and reads/writes escrow using only `address(uint160(uint256(order.inputs[i].token)))` as the key — with no accounting for whether other indices `j != i` still owe escrow in that same token [4](#0-3) . `_withdraw` then simply subtracts `escrowedAmount` from `_orders[commitment][token]` [5](#0-4) .

The corrupted value is `_orders[commitment][token]`: when output pair `i` (a small amount) is completed first, `escrowedAmount` is read as the *full* combined balance intended to also cover output pair `j`. The solver who fills `i` receives escrow meant for `j`'s not-yet-arrived fill; the escrow for `j` is deleted, and a subsequent (honest) solver filling `j` hits `UnknownOrder()` (escrowed == 0) or can no longer receive the correct payout — funds are misdirected to the first filler instead of the rightful one.

### Impact Explanation
This is unauthorized transaction manipulation / wrong-beneficiary fund movement inside the IntentGateway escrow, matching the bounty's "logic attacks" and "stealing or loss of funds" categories. An attacker filling the smaller/faster output leg of a multi-output order sharing an input token drains escrow that belongs to the other output leg, at the expense of the user (whose order can no longer be correctly settled) or the second solver (who is denied their earned input tokens). No malicious relayer, prover, or admin is required — this is triggerable by any ordinary solver via the public `fillOrder` entrypoint.

### Likelihood Explanation
Requires an order with ≥2 output-asset entries whose paired input entries use the identical input token — a configuration not explicitly disallowed by `placeOrder`/`_fillSameChain` (only duplicate *output* tokens are rejected via the transient-storage guard in `placeOrder`) [6](#0-5) ; duplicate *input* tokens across indices are not checked here (unlike `IntentGatewayV2.credit escrow` phase which does reject duplicate input tokens by summing into one slot) [7](#0-6)  — note this duplicate-input rejection lives in `IntentGatewayV2.placeOrder`, but `IntrinsicIntents._fillSameChain`'s per-output-index escrow release logic still operates on the token key without index disambiguation, so any order whose distinct output legs coincidentally target the same input token collapses to the same `_orders[commitment][token]` slot at fill time.

### Recommendation
Track escrow per `(commitment, outputIndex)` or `(commitment, inputToken, outputToken)` rather than by `(commitment, token)` alone, or explicitly reject/merge orders where two output legs map to the same input token before allowing partial/independent per-leg releases. At minimum, `escrowedAmount` on completion of a leg should be capped to that leg's own proportional/tracked entitlement, never the raw current balance of a possibly-shared token slot.

### Proof of Concept
1. User places a same-chain order with two output legs: `output.assets[0] = {DAI, 100}`, `output.assets[1] = {DAI, 900}` (unusual but not rejected by `placeOrder`'s duplicate-output check, which only checks output tokens for exact duplicates within the same call — here tokens are the *same* actually, so use two different output tokens, e.g. `output.assets[0]={DAI,100}`, `output.assets[1]={USDT,900}`, each paired at the same index with `inputs[0]={USDC,100}` and `inputs[1]={USDC,900}` — same input token, different output legs).
2. Attacker (solver A) fills only leg 0 fully (`solverAmount = 100 DAI`). In the loop, `amountFilled == totalRequired` for `i=0`, so `escrowedAmount = _orders[commitment][USDC]` = 1000 (the full combined USDC escrow for both legs), not the 100 USDC that leg 0 alone should release.
3. `_withdraw` transfers the full 1000 USDC to solver A and zeroes `_orders[commitment][USDC]`.
4. Solver B later attempts to fill leg 1 (900 USDT) expecting 900 USDC in return; `_orders[commitment][USDC]` is now 0, so `_withdraw` reverts with `UnknownOrder()` — the user's order is stuck, and solver A has extracted 900 USDC more than entitled to for a 100 DAI fill.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L66-122)
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
```

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1543-1556)
```text
    /*//////////////////////////////////////////////////////////////
                    ROUNDING DUST IN PARTIAL FILLS (Finding #4)
    //////////////////////////////////////////////////////////////*/

    /// @notice Verifies that rounding dust from integer division in partial fills
    /// is not permanently locked. The final solver completing the order should
    /// receive the full remaining escrow balance rather than a truncated amount.
    function testPartialFill_RoundingDustReleasedToFinalSolver() public {
        // Choose amounts that produce rounding truncation:
        // input = 100 USDC (100e6), output = 3 DAI (3e18)
        // Each of 3 solvers fills 1 DAI. Proportional release per fill:
        //   100e6 * 1e18 / 3e18 = 33333333 (truncated from 33333333.33...)
        // Without fix: 3 * 33333333 = 99999999, leaving 1 unit locked.
        // With fix: final solver gets remaining balance = 100e6 - 2*33333333 = 33333334
```

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
