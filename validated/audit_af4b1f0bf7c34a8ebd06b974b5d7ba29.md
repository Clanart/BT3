Found the analog. This is a direct match to the Seaport bug pattern: full order-level fees are paid out to whichever solver happens to trigger `finalize=true`, regardless of how much of the order that particular solver actually filled.

### Title
Entire order's stored transaction fees released to the final partial-filler, not distributed proportionally across all fillers - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`_withdraw()` releases the entire `_orders[commitment][TRANSACTION_FEES]` balance (the Hyperbridge/solver fee collected in full at order placement, see `order.fees` in `IntentGatewayV2.sol`) to the beneficiary of whichever call happens to pass `finalize=true`. For same-chain orders, `_fillSameChain()` in `evm/src/apps/intentsv2/IntrinsicIntents.sol` only sets `isFullyFilled=true` (and therefore `finalize=true`) on the fill that completes the order, exactly mirroring the Seaport bug where a fee sized for the whole order is charged/paid based on nominal order size rather than the actual amount each party filled.

### Finding Description
`order.fees` is collected in full at `placeOrder()` time [1](#0-0)  and stored under the `TRANSACTION_FEES` sentinel key in `_orders[commitment]`.

An order can be filled by multiple different solvers via partial fills, with escrow released proportionally to each: `escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired` [2](#0-1) .

But when `_withdraw()` is called with `finalize=true` (i.e., only on the fill that completes the order), it pays out the *entire* accumulated `TRANSACTION_FEES` balance to that single caller's beneficiary — not a proportional share: [3](#0-2) 

Every fill except the final one is called with `finalize=false` (see `_withdraw(body, false, isFullyFilled)` at [4](#0-3) ), so those solvers get zero fee compensation despite contributing input tokens and gas, while whichever solver happens to submit the final increment collects the *entire* order-level fee that was sized to compensate for filling/relaying the whole order — regardless of how small their own contribution was.

This is structurally identical to the Seaport `_handleFees` bug: a fee amount computed against the full/nominal order size is paid out based on who executes at a particular moment, not on the actual proportion of value that party contributed. In Seaport the overcharge hit the payer; here it's a windfall/starvation split between solvers — an early large filler gets nothing, and a late filler who fills the last 1 wei of the order collects 100% of the accumulated fee.

### Impact Explanation
This causes fund misallocation between legitimate solvers with no protocol benefit:
- A solver who provides 99% of an order's output receives 0% of the collected `order.fees`.
- Any solver — including one who intentionally fills only the last, tiny remaining sliver of an order — can capture the entire fee meant to compensate for the whole fill. This can be exploited by a colluding/sybil solver pair: one solver fills 99% of the order without profit expectation on the fee, then a second address (controlled by the same actor) submits a dust-sized final fill to sweep 100% of `order.fees`.
- This does not steal user funds (the user paid the fee as intended), but it is a logic/accounting flaw that misallocates protocol-intended solver compensation, creating an extractable, gameable value transfer between solvers with no counterbalancing guard in the code.

### Likelihood Explanation
High likelihood of triggering in practice — any order that receives more than one partial fill will exhibit this behavior by default, with no special conditions needed. No malicious relayer, prover, or governance actor is required; any two unprivileged EOAs (or a single actor using two addresses) filling the same order can produce the imbalance. The `surplusShareBps`/protocol-fee code paths in the same file show the team clearly reasons about proportional splits (see `beneficiaryShare`/`protocolShare` logic in `_fillSameChain`), but the `TRANSACTION_FEES` payout path was not made proportional, indicating an overlooked edge case rather than an intentional design decision.

### Recommendation
Track `TRANSACTION_FEES` release proportionally to each partial fill, mirroring the escrow-release calculation already used for `escrowedInputs` (`order.inputs[i].amount * fillAmount / totalRequired`). Concretely, in `_fillSameChain`, compute each filler's proportional share of `_orders[commitment][TRANSACTION_FEES]` based on `fillAmount / totalRequired` (aggregated across all output assets, or normalized against total order value) and pay it out in `_withdraw` on every partial fill, not just on `finalize`.

### Proof of Concept
1. User places a same-chain order with `output.assets[0].amount = 1000`, `order.fees = 100` (fee token units).
2. Solver A fills 999/1000 of the order: `_fillSameChain` computes `fillAmount=999`, `isFullyFilled=false`, calls `_withdraw(body, false, false)` — no fee is released to Solver A despite contributing 99.9% of the fill.
3. Solver B (could be the same actor via a second address) fills the remaining `1` unit: `isFullyFilled=true`, calls `_withdraw(body, false, true)`, which executes: [3](#0-2) 
   Solver B receives the entire `100` fee-token units for filling 0.1% of the order.
4. Repeatable for every order with ≥2 distinct fillers; the imbalance is deterministic, not probabilistic.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L345-359)
```text
        if (order.fees > 0) {
            address feeToken = IDispatcher(hostAddr).feeToken();
            if (msgValue > 0) {
                address uniswapV2 = IDispatcher(hostAddr).uniswapV2Router();
                address WETH = IUniswapV2Router02(uniswapV2).WETH();
                address[] memory path = new address[](2);
                path[0] = WETH;
                path[1] = IDispatcher(hostAddr).feeToken();
                uint256[] memory amounts = IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msgValue}(
                    order.fees, path, address(this), block.timestamp
                );
                msgValue -= amounts[0];
            } else {
                IERC20(feeToken).safeTransferFrom(msg.sender, address(this), order.fees);
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-123)
```text
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```
