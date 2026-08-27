### Title
Attacker-manipulable AMM price sandwiched into `_bullMGP` inflates `mgpAmountToLcok` locked to attacker's vlMGP, funded off manipulated swap output rather than fixed BUSD-to-MGP schedule - ([File: wombat/ArbWomUp2.sol])

### Summary
`incentiveDeposit(_amount, _minMGPRec, true)` lets the caller choose `_minMGPRec`, which is forwarded directly as `amountOutMin` to `ROUTER.swapExactTokensForTokens` inside `_bullMGP`. Since the bonus-adjusted lock amount `mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR` is computed from the raw, attacker-influenceable DEX output `amounts[1]`, an attacker can manipulate the BUSD/MGP pool price around their own call to receive an inflated `amounts[1]` and thus lock disproportionately more MGP into `vlMGP` than the protocol's BUSD-funded incentive schedule intends.

### Finding Description
`incentiveDeposit` computes `rewardToSend` from a fixed WOM-deposit tier schedule [1](#0-0)  and, when `_bullMode` is true, forwards this fixed BUSD amount into `_bullMGP` [2](#0-1) .

Inside `_bullMGP`, the BUSD amount is swapped for MGP via a live AMM router call with the caller-supplied minimum-out, and the bonus is applied to whatever amount the router returns: [3](#0-2) 

Because `_minMGPRec` is a parameter the calling attacker themselves controls (not a protocol-enforced fair-value bound), the attacker faces no incentive to protect against price manipulation that benefits them — they simply set it low/zero. The attacker can then:
1. Flash-loan/acquire MGP or BUSD and trade against the BUSD/MGP router pair to depress the MGP price (front-run).
2. Call `incentiveDeposit(_amount, 0, true)`, causing `_bullMGP`'s swap to execute at the manipulated, favorable price and return an inflated `amounts[1]`.
3. Reverse the initial trade to restore the pool price (back-run), repaying any flash loan, retaining the extra MGP obtained from the pool at manipulated cost plus the `bullBonusRatio` percentage applied on top of that inflated figure.

Existing protections do not stop this: `nonReentrant` and `whenNotPaused` only guard against reentrancy/pausing, not cross-transaction price manipulation, and there is no oracle/TWAP or protocol-set minimum-output check independent of the caller-supplied `_minMGPRec`. The fixed BUSD reward amount from the tier schedule [4](#0-3)  is not manipulable, but the MGP output from spending that BUSD is fully exposed to AMM price manipulation, and the bonus math amplifies whatever swap output results.

### Impact Explanation
This breaks the invariant that MGP locked into `vlMGP` should be backed by/proportional to the protocol's BUSD incentive budget at fair market rate. An attacker can extract more MGP from the router pool's liquidity (at the LPs'/pool's expense) than the campaign intended to pay for, then receive the `bullBonusRatio` bonus on top of that already-inflated amount, locking an outsized vlMGP position for themselves. Since vlMGP typically carries governance/voting weight, this also risks disproportionate voting power gained through price manipulation rather than genuine WOM contribution, in addition to depleting the protocol's BUSD-funded incentive/backing budget faster than designed.

### Likelihood Explanation
The attack requires only unprivileged access: an EOA (or attacker-deployed contract) that can trade against the BUSD/MGP router pair and call the public `incentiveDeposit` function with `_bullMode=true` and a self-chosen `_minMGPRec`. Capital can be sourced via flash loan on the same block, making the round-trip price manipulation and the `incentiveDeposit` call atomically composable and repeatable for every incentive campaign deposit, limited only by available `busd` balance in the contract used per swap.

### Recommendation
Do not apply `bullBonusRatio` to a live, attacker-influenced DEX swap output. Either (a) use a protocol-controlled minimum output / oracle-derived fair price bound for the swap rather than caller-supplied `_minMGPRec`, and/or (b) compute the bonus based on a fixed BUSD-to-MGP reference rate (e.g., TWAP or governance-set rate) rather than the instantaneous `amounts[1]` from `swapExactTokensForTokens`, and cap the acceptable deviation between spot and reference price before allowing the bull-mode lock to proceed.

### Proof of Concept
Foundry fork test plan:
1. Fork BSC mainnet at a block where the BUSD/MGP Pancake pair has real liquidity and `ArbWomUp2` is configured with `busd`, `mgp`, `vlMGP`, `bullBonusRatio`, and `ROUTER` set via `setup`.
2. Baseline run: call `incentiveDeposit(amount, 0, true)` from an attacker EOA with no pool manipulation; record `mgpAmountToLcok` emitted in `VLMGPRewarded`.
3. Manipulated run (fresh fork state): attacker flash-loans BUSD/MGP, sells MGP into the pool to depress MGP price, calls `incentiveDeposit(amount, 0, true)` in the same block, then buys back MGP to restore price and repays the flash loan.
4. Assert `mgpAmountToLcok` in the manipulated run significantly exceeds the baseline `mgpAmountToLcok` for the same `_amount`/`rewardToSend`, while the attacker's net BUSD/MGP round-trip cost (fees + slippage) is smaller than the extra vlMGP value gained, proving profitable price manipulation of the bonus-scaled lock amount.

### Citations

**File:** wombat/ArbWomUp2.sol (L87-89)
```text
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
        _deposit(_amount);
        claimedReward[msg.sender] += rewardToSend;
```

**File:** wombat/ArbWomUp2.sol (L91-92)
```text
        if (_bullMode) {
            _bullMGP(rewardToSend, _minMGPRec, msg.sender);
```

**File:** wombat/ArbWomUp2.sol (L99-117)
```text
    function getRewardAmount(uint256 _amount, address _account) external view returns (uint256) {
        if (_amount == 0 || rewardMultiplier.length == 0) return 0;
        uint256 accumulated = _amount + userWOMDeposited[_account];

        uint256 rewardAmount = 0;
        uint256 i = 1;
        while (i < rewardTier.length && accumulated > rewardTier[i]) {
            rewardAmount +=
                (rewardTier[i] - rewardTier[i - 1]) *
                rewardMultiplier[i - 1];
            i++;
        }
        rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];

        uint256 busdReward = (rewardAmount / DENOMINATOR) - this.calDoubledCounted(_account);
        uint256 busdleft = IERC20(busd).balanceOf(address(this));

        return busdReward > busdleft ? busdleft : busdReward;
    }
```

**File:** wombat/ArbWomUp2.sol (L162-180)
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
```
