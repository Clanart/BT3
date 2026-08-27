### Title
Unprotected spot-price swap in `_bullMGP` allows price manipulation to inflate locked MGP bonus - ([File: wombat/ArbWomUp2.sol])

### Summary
`ArbWomUp2._bullMGP` swaps BUSD for MGP directly against `ROUTER.swapExactTokensForTokens` using the caller-supplied `_minRec` as the only slippage guard, then computes the vlMGP lock amount as a **linear multiple of the raw swap output** (`amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR`). Because `incentiveDeposit` lets the attacker pass `_minMGPRec = 0`, there is no protection preventing the swap from executing at an attacker-manipulated pool price, so an attacker can depress the BUSD/MGP pool price immediately before the swap to inflate `amounts[1]`, which directly and proportionally inflates the bonus MGP locked to their own `vlMGP` position.

### Finding Description
The call path is: `incentiveDeposit(_amount, _minMGPRec, true)` → `_bullMGP(rewardToSend, _minMGPRec, msg.sender)`. [1](#0-0) 

Inside `_bullMGP`, the swap has no TWAP or oracle-based fair-value check; `_minRec` is fully attacker-controlled (it is passed straight through from `incentiveDeposit`'s `_minMGPRec` argument, which the attacker sets to `0`), so the swap will settle at whatever spot price the pool has at execution time: [2](#0-1) 

The bonus/lock amount `mgpAmountToLcok` is a direct linear function of the raw AMM output `amounts[1]`. If the attacker temporarily depresses the MGP price in the BUSD/MGP pool used by `ROUTER` (e.g., sell MGP into the pool, or via a flash-loan-funded large swap) immediately before triggering `incentiveDeposit(..., 0, true)`, the same `_busdAmount` will convert into a larger `amounts[1]`, which is then multiplied by `(DENOMINATOR + bullBonusRatio)` and locked to the attacker's `vlMGP` position via `vlMGP.lockFor`. Since the pre-approved MGP being locked comes from the contract's own MGP inventory (`IERC20(mgp).approve(address(vlMGP), mgpAmountToLcok)`), any amount beyond the "fair value" swap output is extracted from protocol-held MGP reserves that fund the bull-mode bonus. The attacker restores the pool price afterward (or the arbitrage self-corrects), completing an atomic, reentrancy-free, single-transaction manipulation. Existing protections — `nonReentrant`, `whenNotPaused`, `_checkAmount` — do not address price-manipulation risk; none of them enforce a TWAP or bound the swap against a fair reference price.

### Impact Explanation
This allows theft of protocol-held MGP inventory reserved for the bull-mode bonus: an attacker inflates the amount of MGP locked to their own `vlMGP` position beyond what their BUSD reward is fairly worth, directly reducing the MGP available to back other users' bull-mode bonuses and depleting protocol reserves. This matches the "theft of unclaimed yield / protocol inventory" impact class, since the excess locked MGP is a real, redeemable (after unlock) asset extracted at the expense of the protocol.

### Likelihood Explanation
The attack requires only: (1) capital to skew the BUSD/MGP pool used by `ROUTER` (obtainable via flash loan), (2) the ability to call the public `incentiveDeposit` function with `_minMGPRec = 0`, and (3) enough WOM tokens to generate a nonzero `rewardToSend` from `getRewardAmount`. All of this is achievable by an unprivileged EOA/contract in a single atomic transaction, and is repeatable as long as `bullBonusRatio > 0` and the pool has exploitable liquidity depth relative to the attacker's flash-loan size.

### Recommendation
Add a fair-value check to `_bullMGP`, e.g. compare `amounts[1]` (or the pre-swap expected output) against a TWAP-derived reference price and cap the bonus computation to the TWAP-implied amount, or require `_minMGPRec` to be validated against an on-chain TWAP rather than trusting the caller-supplied value. Alternatively, decouple the bonus calculation from the manipulable spot swap output entirely (e.g., base it on a fixed BUSD-denominated rate rather than `amounts[1]`).

### Proof of Concept
Foundry test plan:
1. Deploy `ArbWomUp2` fork-forked against the real BUSD/MGP pool used by `ROUTER`, funded with MGP bonus inventory and configured `bullBonusRatio`.
2. Set up an attacker contract that: (a) takes a flash loan, (b) swaps a large amount of MGP → BUSD in the pool to depress MGP price, (c) calls `incentiveDeposit(_amount, 0, true)`, capturing `mgpAmountToLcok` from the emitted `VLMGPRewarded` event, (d) swaps back to restore pool price, (e) repays the flash loan.
3. Compute a TWAP-fair `mgpAmountToLcok` using the pre-manipulation price and assert the actual locked amount from step 2c significantly exceeds the TWAP-fair amount.
4. Assert protocol's MGP balance decreased by more than the fair-value bonus, quantifying the stolen/drained MGP inventory.

### Citations

**File:** wombat/ArbWomUp2.sol (L82-97)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _minMGPRec, bool _bullMode
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;

        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
        _deposit(_amount);
        claimedReward[msg.sender] += rewardToSend;
        
        if (_bullMode) {
            _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        } else {
            IERC20(busd).transfer(msg.sender, rewardToSend);
            emit BUSDRewarded(msg.sender, rewardToSend);
        }
    }
```

**File:** wombat/ArbWomUp2.sol (L162-181)
```text
    function _bullMGP(uint256 _busdAmount, uint256 _minRec, address _account) internal {
        IERC20(busd).safeApprove(address(ROUTER), _busdAmount);
        
        address[] memory path = new address[](2);
        path[0] = busd;
        path[1] = mgp;
        uint256[] memory amounts = ROUTER.swapExactTokensForTokens(
            _busdAmount,
            _minRec,
            path,
            address(this),
            block.timestamp
        );

        uint256 mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR; // get bull mode bonus
        IERC20(mgp).approve(address(vlMGP), mgpAmountToLcok);
        vlMGP.lockFor(mgpAmountToLcok, _account);

        emit VLMGPRewarded(_account, _busdAmount, mgpAmountToLcok);
    }
```
