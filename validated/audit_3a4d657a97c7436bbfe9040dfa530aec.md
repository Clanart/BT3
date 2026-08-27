### Title
Reward multiplier arbitrage via `ArbWomUp3.getRewardAmount` non-lock path missing incremental tiering/double-count deduction - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.getRewardAmount` computes rewards differently depending on `_lock`: the `_lock=true` (mode=2) branch correctly walks the tier brackets incrementally and subtracts `calDoubledCounted(_account)` to avoid re-paying for already-rewarded WOM, but the `_lock=false` (mode=0/1) branch simply multiplies the *entire* `_amountToConvert` by `rewardMultiplier[getUserTier(_account)]` with no bracket weighting and no double-count deduction at all. Because `getUserTier` is driven purely by `mWomSV.getUserTotalLocked(_account)`, an attacker can reach a high tier once via a mode=2 lock and then repeatedly call `incentiveDeposit` with mode=0/1 to have arbitrarily large additional WOM amounts rewarded at the full top-tier multiplier, draining the fixed MGP incentive pool far beyond the intended tiered schedule.

### Finding Description
`getUserTier` reads the user's real locked balance in `mWomSV` via `mWomSV.getUserTotalLocked(_account)`: [1](#0-0) 

Mode=2 in `_deposit` genuinely locks real mWom for the account in `mWomSV` (converting half via `smartWomConvert` and locking the resulting mWom balance): [2](#0-1) 

For the lock branch (`_lock == true`), `getRewardAmount` correctly computes the reward incrementally across `rewardTier` brackets over `accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account)` and subtracts `calDoubledCounted(_account)` (the reward already attributable to the existing locked balance) so previously-rewarded WOM is not double counted: [3](#0-2) 

For the non-lock branch (`_lock == false`, i.e., mode=0/1), the reward is instead computed as a flat multiplication of the entire new `_amountToConvert` by `rewardMultiplier[getUserTier(_account)]`, with no bracket weighting whatsoever and no `calDoubledCounted` subtraction: [4](#0-3) 

Mode=0/1 in `_deposit` never touches `mWomSV`, so `getUserTier(_account)` stays fixed at whatever tier the attacker's `mWomSV` lock puts them in: [5](#0-4) 

Exploit flow: the attacker calls `incentiveDeposit(largeAmt, ratio, false, 2)` once, locking real mWom into `mWomSV` and reaching a high `getUserTier` bracket (with the mode=2 path itself already granting a 2x bonus per `incentiveDeposit`'s `rewardToSend * 2` logic). Because that tier is now fixed and independent of any subsequent non-locked contribution, the attacker repeatedly calls `incentiveDeposit(hugeAmt, ratio, false, 0)`. Each such call computes `hugeAmt * rewardMultiplier[topTier] / DENOMINATOR` as the MGP reward — applying the top-tier rate to the *entire* new amount every single time, rather than the intended blended/incremental rate that a genuinely-accumulated deposit of that size would receive, and without ever deducting what was already accounted for by the mWomSV lock. The only cap is the contract's own MGP balance (`mgpleft` check), so the attack drains the finite incentive pool disproportionately.

Existing checks do not prevent this: `whenNotPaused`/`nonReentrant` on `incentiveDeposit` do not restrict repeated calls, `calDoubledCounted` is only applied in the lock branch, and there is no accounting elsewhere that ties a user's mode=0/1 conversion volume to their tier bracket incrementally.

### Impact Explanation
This results in over-issuance of MGP from the fixed incentive balance held by `ArbWomUp3` (`IERC20(mgp).balanceOf(address(this))`), which is meant to be distributed according to a tiered incremental schedule. An attacker can extract MGP at the maximum tier multiplier on unlimited additional WOM conversions after a single real mWomSV lock, depleting the reward pool that should otherwise remain available for other genuine participants — this is theft of unclaimed MGP yield / protocol insolvency of the incentive program (Immunefi: theft of unclaimed yield / protocol insolvency).

### Likelihood Explanation
The attacker needs no privileged role — only capital to fund one `convertAndLock`/`incentiveDeposit(mode=2)` call to reach a tier, and WOM tokens for subsequent mode=0/1 calls. This is fully repeatable (bounded only by remaining MGP balance in the contract) and does not require flash loans, reentrancy, or governance/admin access, making it straightforward to execute by any EOA holding WOM.

### Recommendation
Make the non-lock (`_lock == false`) branch of `getRewardAmount` compute rewards using the same incremental bracket-weighted logic as the lock branch, based on the user's actual cumulative *non-locked* contribution (tracked per-account, similar to `userWOMDeposited` in `ArbWomUp`/`ArbWomUp2`), and subtract an equivalent "already rewarded" amount so repeated calls at a fixed tier cannot re-apply the top multiplier to unlimited additional volume. Do not derive the non-lock reward multiplier solely from `mWomSV.getUserTotalLocked`, since that value is unrelated to how much WOM has actually been converted through the non-lock path.

### Proof of Concept
Foundry test plan:
1. Deploy `ArbWomUp3`, mock `mWom`, `mWomSV` (with real `depositMWomSVFor`/`getUserTotalLocked` semantics), `smartWomConvert`, `vlMGP`, and fund `ArbWomUp3` with a large MGP balance.
2. Configure `rewardTier`/`rewardMultiplier` via `setMultiplier` with at least 3 brackets, top bracket having a materially higher multiplier.
3. Attacker calls `incentiveDeposit(tierThresholdAmt, ratio, false, 2)`; assert `getUserTier(attacker) == topTier` afterward and record MGP locked into `vlMGP` for attacker (call #1 reward, doubled per mode=2 bonus).
4. Attacker calls `incentiveDeposit(hugeAmt, ratio, false, 0)`; assert the MGP reward equals `hugeAmt * rewardMultiplier[topTier] / DENOMINATOR` (i.e., the full top-tier rate on the entire new amount), and compare against what the correct incremental calculation (as used in the `_lock==true` branch) would have produced for the same cumulative total — showing a materially larger payout.
5. Repeat step 4 multiple times, showing MGP balance of `ArbWomUp3` is drained disproportionately fast relative to a baseline scenario where the same cumulative WOM volume is deposited entirely through mode=1 without ever locking in `mWomSV` first.
6. Optionally, have the attacker call `mWomSV.startUnlock`/`unlock` after cooldown to show they can recover their originally locked mWom while having already extracted the excess MGP, demonstrating the reward extraction is not offset by any loss of principal.

### Citations

**File:** wombat/ArbWomUp3.sol (L107-111)
```text
    function getRewardAmount(uint256 _amountToConvert, address _account, bool _lock) external view returns (uint256) {
        uint256 mgpReward = 0;

        if (!_lock) {
            mgpReward = _amountToConvert * rewardMultiplier[getUserTier(_account)] / DENOMINATOR;
```

**File:** wombat/ArbWomUp3.sol (L112-124)
```text
        } else {
            uint256 accumulated = _amountToConvert + mWomSV.getUserTotalLocked(_account);
            uint256 rewardAmount = 0;
            uint256 i = 1;

            while (i < rewardTier.length && accumulated > rewardTier[i]) {
                rewardAmount +=
                    (rewardTier[i] - rewardTier[i - 1]) *
                    rewardMultiplier[i - 1];
                i++;
            }
            rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];
            mgpReward = (rewardAmount / DENOMINATOR) - calDoubledCounted(_account);
```

**File:** wombat/ArbWomUp3.sol (L146-155)
```text
    function getUserTier(address _account) public view returns (uint256) {
        uint256 userMWOMSVBal = mWomSV.getUserTotalLocked(_account);
        for (uint256 i = tierLength - 1; i >= 1; i--) {
            if (userMWOMSVBal >= rewardTier[i]) {
                return i;
            }
        }

        return 0;
    }
```

**File:** wombat/ArbWomUp3.sol (L183-209)
```text
        if (_mode == 1) {
            IERC20(wom).safeApprove(mWom, _amount);
            IMWom(mWom).deposit(_amount);            
            IERC20(mWom).safeApprove(smartWomConvert, _amount);
            IConverter(smartWomConvert).depositFor(_amount, _account);

        } else if (_mode == 2) {
            uint256 toDeposit = _amount / 2;
            uint256 toSwap = _amount - toDeposit;

            // 50% goes to deposit
            IERC20(wom).safeApprove(mWom, toDeposit);
            IMWom(mWom).deposit(toDeposit); 

            // 50% smart smart convert
            IERC20(wom).safeApprove(smartWomConvert, toSwap);
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);

            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);

        } else {
            IERC20(wom).safeApprove(mWom, _amount);
            IMWom(mWom).deposit(_amount);               
            IERC20(mWom).safeTransfer(_account, _amount);
        }
```
