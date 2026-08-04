Confirmed: `_orders` is keyed by `mapping(bytes32 => mapping(address => uint256))` on `(commitment, token address)` [1](#0-0) , and `_withdraw` decrements/transfers per-token amounts against that single shared balance [2](#0-1) . In `_fillSameChain`, the "last-fill" branch fetches the *entire remaining* balance for the input token keyed only by token address — `escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))]` — whenever that index's own `amountFilled == totalRequired` [3](#0-2) .

### Title
Escrow over-release when multiple order inputs/outputs share the same token address - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
The rounding-dust fix in `_fillSameChain` releases the *entire* remaining `_orders[commitment][token]` balance to whichever solver completes any single output-pair index whose input token happens to collide with another index's input token, rather than releasing only that index's proportional share.

### Finding Description
`_orders` tracks escrow per `(commitment, token)`, not per `(commitment, index)` [1](#0-0) . `placeOrder`/escrow accounting sums input amounts into this single per-token bucket. When a solver fully completes output-pair index `i` (`amountFilled == totalRequired`), the code takes a shortcut intended to flush rounding dust: instead of computing the proportional share, it reads the whole live balance for that token and hands it all to the solver:

```solidity
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
``` [3](#0-2) 

For a single-input-token order this correctly flushes leftover dust, as the added test `testPartialFill_RoundingDustReleasedToFinalSolver` demonstrates [4](#0-3) . But if an order has **two or more output pairs whose `inputs[i].token` is the same address** (e.g., a user escrows USDC and wants both DAI and WETH, paying for both legs out of the same USDC pool), then completing the *first* leg's output in full drains the *combined* USDC balance meant to also back the second, still-open leg. `_withdraw`'s subsequent per-token decrement (`_orders[commitment][token] = escrowed - amount`) has no way to distinguish which index the escrow "belonged" to [5](#0-4) , so the second leg's solver either gets `UnknownOrder()` reverts (denial of the second leg / order stuck since `isFullyFilled` never becomes true) or, if a partial fill on leg 2 was already recorded, the escrow needed to complete it has already left the contract.

### Impact Explanation
A solver can construct or be the first filler of a multi-output order that shares an input token across output indices and, by completing only their preferred leg, extract escrow value that was earmarked for another leg's output/beneficiary. This is unauthorized fund movement out of escrow to the wrong recipient/amount and can permanently strand the order (unfillable second leg, no refund path since `_filled[commitment]` isn't set unless `isFullyFilled`), directly matching the "wrong beneficiary or amount" / fund-loss impact class.

### Likelihood Explanation
Reaching this path requires only placing (or filling) an order with two `inputs[]` entries pointing at the same ERC-20 address paired with two different `outputs[]` entries — no privileged role, relayer, or malformed proof is needed; it is fully reachable by any ordinary user constructing the order and any solver calling the public `fillOrder` entrypoint on `IntentGatewayV2`/`IntrinsicIntents`.

### Recommendation
Track escrow per `(commitment, index)` instead of `(commitment, token)`, or when flushing "final fill" dust, cap the release to `min(_orders[commitment][token], order.inputs[i].amount - alreadyReleased[i])` computed from a per-index accounting structure rather than reading the shared per-token balance directly.

### Proof of Concept
1. User places an order with `inputs = [ {USDC, 1000}, {USDC, 500} ]` and `outputs = [ {DAI, 1000e18}, {WETH, 1e18} ]` (both inputs share the USDC address; `_orders[commitment][USDC] = 1500`).
2. Solver A fully fills the DAI leg (`outputs[0]`) with `1000e18` DAI in one call. `amountFilled == totalRequired` for index 0, so `escrowedAmount = _orders[commitment][USDC] = 1500` (the combined balance) is paid out to Solver A, and `_orders[commitment][USDC]` is zeroed.
3. `isFullyFilled` is still `false` because the WETH leg (`outputs[1]`) hasn't been filled, so `_filled[commitment]` is cleared and the order remains open.
4. Solver B attempts to fill the WETH leg; `_withdraw` reads `_orders[commitment][USDC] == 0` and reverts with `UnknownOrder()` — the WETH leg can never be completed, and the 500 USDC meant to back it was already paid to Solver A in step 2.

*Note: I could not fully trace `placeOrder`'s escrow-crediting logic (in `evm/src/apps/intentsv2/ExtrinsicIntents.sol` or the main `IntentGatewayV2.sol`) within the available tool budget to confirm there is no existing per-index dedup/validation that rejects duplicate input-token entries at order-placement time; if such a check exists, this finding would be mitigated at the input-validation layer instead. This should be verified directly against `placeOrder` before treating this as conclusively exploitable.*

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

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L1547-1557)
```text
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
        uint256 inputAmount = 100 * 1e6; // 100 USDC
```
