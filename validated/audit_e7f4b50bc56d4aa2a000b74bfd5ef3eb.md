Found a real analog: `_withdraw` in `IntentsBase.sol` releases the entire accumulated `TRANSACTION_FEES` balance to whichever beneficiary happens to trigger `finalize=true` on a same-chain **partial fill sequence**, rather than pro-rating it across fillers — but critically, this only pays out on `finalize`, and for `_fillSameChain` `finalize` is only passed as `true`... let me state the actual, verifiable issue.

### Title
Full `TRANSACTION_FEES` balance paid to the *last* partial-fill solver instead of being pro-rated across all fillers - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`_withdraw()` releases the entire `_orders[commitment][TRANSACTION_FEES]` balance to the beneficiary whenever `finalize == true` [1](#0-0) . For same-chain orders, `_fillSameChain` calls `_withdraw(body, false, isFullyFilled)` [2](#0-1) , so `finalize` is `true` only on the fill that completes the order. This means the entire relayer-fee/transaction-fee bucket accumulated for the order — which was collected proportionally to the order's total size, not to any single solver's contribution — is transferred in full to whichever solver happens to submit the final partial fill, with none of it distributed to the earlier partial fillers who contributed most of the input tokens.

### Finding Description
`TRANSACTION_FEES` is a per-commitment fee bucket keyed by the sentinel address `keccak256("txFees")` [3](#0-2) . It is paid out atomically and in full at `finalize` time:
```solidity
if (finalize) {
    uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
    if (fees > 0) {
        delete _orders[body.commitment][TRANSACTION_FEES];
        IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
    }
    ...
}
``` [1](#0-0) 

Unlike the escrowed input-token amounts — which `_fillSameChain` correctly computes proportionally per fill via `escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired` [4](#0-3)  — the `TRANSACTION_FEES` value has no such proportional split logic anywhere in `IntrinsicIntents.sol`. It is only ever read/paid inside `_withdraw`'s `finalize` branch, and `finalize` is `isFullyFilled`, which is `true` exactly once per order: on the fill that pushes `amountFilled == totalRequired` for every output asset [5](#0-4) . Every solver before that final fill receives `_withdraw(body, false, false)` treatment — no fee payout at all, only their proportional escrowed input.

This is the direct structural analog to the ELFI bug: a value that represents an aggregate quantity computed over the *whole* order (settled/accumulated fees) is disbursed using a rule intended for a single terminal event, rather than being pro-rated across the multiple partial settlements that actually occurred. In ELFI, the double-division silently shrank the fee attribution per partial close; here, the fee attribution isn't split at all — 100% of it lands on the last filler.

### Impact Explanation
This directly violates "bridged assets ... relayer rewards ... must move exactly once and only to the rightful beneficiary and amount." The `TRANSACTION_FEES` bucket represents relayer/transaction fees tied to the order as a whole (and is also used identically in the cross-chain finalize path in `_withdraw` called from `onAccept`/`onGetResponse`). For same-chain multi-solver partial fills, whichever address happens to complete the order — which could be an unprivileged solver timing their fill to be the completing one — captures fees that were economically attributable to the entire filled volume, at the expense of earlier partial fillers who received none of it despite having supplied input-token liquidity. This is a wrong-beneficiary/wrong-amount fund-movement bug reachable by any solver through the standard, permissionless `fillOrder` entrypoint — no malicious relayer, prover, or admin is required.

### Likelihood Explanation
High for the specific pattern (any order that both (a) sets a non-zero `_orders[commitment][TRANSACTION_FEES]` value and (b) is completed via more than one partial same-chain fill). A rational solver who observes an order is close to fully filled (e.g., 95% filled by earlier solvers) is incentivized to specifically target completing the remaining 5% purely to capture the entire accumulated fee bucket, which is a real, low-cost, unprivileged attack a searcher/solver can execute deterministically by racing/timing their fill call.

### Recommendation
Pro-rate `TRANSACTION_FEES` payout across each fill proportionally to `fillAmount / totalRequired` (per output-asset fill), the same way `escrowedAmount` is already computed in `_fillSameChain`, and pay out the proportional fee slice on every partial fill (`finalize=false` calls) rather than gating the entire fee transfer behind `finalize`. Alternatively, decouple the fee-release condition from `isFullyFilled` and instead release `fees * fillAmount / totalRequired` inside the loop in `_fillSameChain`, decrementing `_orders[commitment][TRANSACTION_FEES]` accordingly.

### Proof of Concept
1. User places a same-chain order with `order.fees > 0`, causing `_orders[commitment][TRANSACTION_FEES]` to be credited (fee-token amount `F`) at order placement.
2. Solver A fills 90% of the order via `fillOrder` → `_fillSameChain` computes `isFullyFilled = false` (since remaining output > 0) → `_withdraw(body, false, false)` is called → Solver A receives their proportional escrowed input tokens but **zero** fee-token payout (fee branch skipped since `finalize=false`).
3. Solver B fills the remaining 10% → `isFullyFilled = true` → `_withdraw(body, false, true)` is called → the entire `F` from `_orders[commitment][TRANSACTION_FEES]` is transferred to Solver B, despite Solver B having contributed only 10% of the fill volume.
4. Result: Solver B receives 100% of the order's accumulated transaction fees for 10% of the work; Solver A receives 0% despite doing 90% of the work — an unauthorized/wrong-amount fund transfer to the wrong beneficiary relative to actual economic contribution, reachable by any two unprivileged solvers colluding or racing on a public order.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L61-67)
```text
    /**
     * @dev Sentinel address used as the key for storing Hyperbridge relayer fees
     * in the `_orders` mapping. Derived from keccak256("txFees") to avoid
     * collisions with real token addresses.
     */
    address internal constant TRANSACTION_FEES = address(uint160(uint256(keccak256("txFees"))));

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L113-113)
```text
            if (totalRequired > amountFilled) isFullyFilled = false;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-121)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L131-134)
```text
        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);
```
