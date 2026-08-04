### Title
Full-fill escrow release reads the aggregate per-token balance instead of the per-leg allocation, letting a solver drain escrow backing other unfilled legs - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`_fillSameChain()` computes the input escrow to release to a solver differently depending on whether a leg is "fully filled" or "partially filled." For a partial fill it correctly computes a proportional share (`order.inputs[i].amount * fillAmount / totalRequired`), but for a full fill it instead releases the entire current balance of `_orders[commitment][token]` [1](#0-0) . Because `_orders` is keyed only by `(commitment, token address)` and not by output leg index [2](#0-1) , any order whose `inputs[]` array uses the same token address to back more than one output leg has all of that token's escrow collapsed into a single aggregate balance. Fully filling just one such leg pays out the *entire* aggregate balance for that token, not the fraction backing that specific leg — draining escrow that was meant to back the other, still-unfilled legs of the same order.

### Finding Description
`_fillSameChain` tracks per-leg fill progress in `_partialFills[commitment][outputToken]`, but the escrow it releases is tracked per-token in `_orders[commitment][token]`, an aggregate across all legs that reference that token [3](#0-2) .

The full-fill branch:
```
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
``` [1](#0-0) 

reads the *whole remaining balance* for that token address rather than the amount that was actually escrowed to back this particular leg (`order.inputs[i].amount`). This is exactly the class of bug described in the external report: a proportional/allocation-aware accounting value (`_partialFills`, per-leg) diverges from the value actually consumed on payout (`_orders`, per-token aggregate), letting more be withdrawn than was truly allocated to the thing being settled.

Concretely, for an order with two output legs both backed by input token `T` (e.g. `inputs = [{T,600},{T,400}]`, `outputs=[{X,600},{Y,400}]`), `_orders[commitment][T] = 1000` after `placeOrder`. If a solver fully fills leg 0 (output `X`, 600) in one shot, `amountFilled == totalRequired` for that leg, so `escrowedAmount = _orders[commitment][T] = 1000` — the *entire* pool, not the 600 that was meant to back leg 0. `_withdraw` then decrements `_orders[commitment][T]` by 1000, zeroing it [4](#0-3) . Since leg 1 (`Y`, 400) is still unfilled, `isFullyFilled` is `false`, so `_filled[commitment]` is deleted and the order remains "open" for further fills/cancellation [5](#0-4) . Any subsequent attempt to fill leg 1 or to cancel the order finds `_orders[commitment][T] == 0` and reverts with `UnknownOrder` in `_withdraw`/`_cancelSameChain` [6](#0-5) [7](#0-6) , meaning the escrow meant to back leg 1 was already paid out to the leg-0 solver, and the user's leg-1 funds are gone with no path to fill or refund.

Existing guards do not stop this: `_cancelSameChain` only checks whether *any* escrow remains (`hasEscrow`), not whether the released amount matched what should remain per-leg [8](#0-7) , and there is no invariant check anywhere that `sum(_orders[commitment][token] released) == sum(per-leg allocations for that token)`.

### Impact Explanation
This falls squarely under "stealing or loss of funds" and "logic attacks" in the Hyperbridge bounty scope: a solver (an unprivileged, ordinary participant in the intents flow — not a relayer/prover/admin) can, through ordinary `fillOrder` calls, extract escrow that was allocated to back other legs of the same order, at the expense of the user who placed the order and any subsequent solver expecting to fill the remaining leg. The remaining leg becomes permanently unfulfillable and the corresponding user funds unrecoverable (`_withdraw` reverts once the aggregate balance hits zero), which is a direct loss/lock of bridged/escrowed funds in production intent-settlement code.

### Likelihood Explanation
The precondition is that a legitimate order intentionally splits one input token across multiple output legs (e.g., "swap 1000 USDC into 600 DAI + 400 WETH" in a single order) — a normal, unrestricted use of the `Order.inputs`/`Order.output.assets` arrays; nothing in `placeOrder`/`IntentsBase` enforces a 1:1 unique-token mapping between input and output legs. Any solver filling such an order leg-by-leg (which the contract explicitly supports via partial fills) can trigger the full-balance read on the first leg it completes. This requires no special privilege, malicious peer, or front-running — just calling `fillOrder` with `solverAmount == totalRequired` for one leg of a multi-leg, same-token order.

### Recommendation
Track escrow allocation per output leg (or per `(commitment, inputIndex)`) rather than only per `(commitment, token)`. In the full-fill branch of `_fillSameChain`, release exactly `order.inputs[i].amount` (the leg's original allocation, adjusted for any amount already consumed by earlier partial fills of that same leg) instead of the full current `_orders[commitment][token]` balance. Alternatively, disallow orders whose `inputs[]` contains duplicate token addresses across different legs, or maintain a separate per-leg-reserved mapping that `_withdraw`/`_cancelSameChain` decrement against instead of the shared aggregate.

### Proof of Concept
1. User places a same-chain order with `inputs = [{token: USDC, amount: 600}, {token: USDC, amount: 400}]` and `output.assets = [{token: DAI, amount: 600}, {token: WETH, amount: 400}]`. `_orders[commitment][USDC] = 1000`.
2. Solver A calls `fillOrder` supplying `solverAmount = 600` for the DAI leg only (leg index 0). `amountFilled(600) == totalRequired(600)` → `escrowedAmount = _orders[commitment][USDC] = 1000` is released to solver A via `_withdraw`, zeroing `_orders[commitment][USDC]`.
3. `isFullyFilled` is `false` (WETH leg unfilled), so `_filled[commitment]` is deleted, order appears still open.
4. Any solver attempting to fill the WETH leg, or the user attempting `_cancelSameChain`, finds `_orders[commitment][USDC] == 0` and reverts with `UnknownOrder`, permanently losing the 400 USDC that should have backed the WETH leg — funds effectively stolen by solver A beyond its entitled share.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L113-141)
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
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-181)
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-152)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;

    /**
     * @dev Maps keccak256(stateMachineId) to the registered gateway address for
     * that chain. Used for authenticating cross-chain messages and routing dispatches.
     */
    mapping(bytes32 => address) public _instances;

    /**
     * @dev Maps (commitment, output token) to the cumulative amount already filled.
     * Used to track partial fill progress for same-chain orders.
     */
    mapping(bytes32 => mapping(bytes32 => uint256)) public _partialFills;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L400-403)
```text
            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```
