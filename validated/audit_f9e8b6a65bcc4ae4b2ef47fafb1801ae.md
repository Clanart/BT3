### Title
Unrestricted user-selectable `_mode` lets any depositor double their MGP reward for equal cost - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.incentiveDeposit` lets any wallet freely choose the `_mode` parameter of its own deposit. Choosing `_mode == 2` causes the contract to double the computed MGP reward before locking it for the caller, with no additional cost or restriction versus `_mode == 1`. This mirrors the reported `AccessTokenContract.paidMint` vs `referralMint` bug class: two publicly reachable paths exist for the same underlying action, one of which grants a strictly larger payout that a rational actor will always pick, draining the finite reward pool faster and more unfairly than the tiered design intends.

### Finding Description
`incentiveDeposit` is a fully public, unprivileged entry point: [1](#0-0) 

The reward is computed by `getRewardAmount(_amount, msg.sender, _mode == 2)`, then unconditionally multiplied by 2 whenever the caller passes `_mode == 2`:
```solidity
uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);
// giving out 50% more bonus
if (_mode == 2)
    rewardToSend = rewardToSend * 2;
```
The in-code comment claims a "50% more bonus" (i.e. `*1.5`), but the implementation applies `*2` (100% more), an internal inconsistency indicating this doubling was not the intended magnitude of the incentive.

Crucially, the caller supplies `_mode` themselves with no on-chain restriction tying it to any real cost difference or eligibility check, exactly as the referral report describes for `winChance`: the caller "sets himself" the higher-value path. In `_deposit`, `_mode == 2` only changes how the same input `_amount` of WOM is routed (half deposited via `mWom`, half swapped/locked via `smartWomConvert`) — it does not require the user to commit any more capital than `_mode == 1` or the default branch: [2](#0-1) 

Since every unprivileged wallet can call `incentiveDeposit` directly and always pass `_mode = 2`, every depositor can unconditionally claim double the intended MGP reward for the same WOM contribution.

### Impact Explanation
The MGP payout for this incentive program is bounded only by the contract's own MGP balance (`mgpleft` cap in `getRewardAmount`), which is a finite pool the protocol pre-funds for this airdrop/incentive distribution. Because every rational unprivileged wallet will always select `_mode == 2` to receive double reward at no extra cost, the finite reward pool is consumed roughly 2x faster than the tier design intended, and the doubled payouts are effectively pulled from the shared incentive/airdrop allocation meant to be distributed fairly according to the tiered `rewardMultiplier`/`rewardTier` schedule. This results in the protocol handing out MGP it did not intend to (a business-logic-bypass extraction of protocol-held reward funds), consistent with the accepted "direct theft of user/protocol funds" impact class, since honest users following the intended tiering are shortchanged once the pool is depleted by mode-2 farmers.

### Likelihood Explanation
Likelihood is high: `incentiveDeposit` is a standard external function callable by any wallet with no privileged role, and choosing `_mode == 2` requires no special setup beyond holding WOM — the same capital already needed for `_mode == 1`. There is no incentive misalignment discouraging this choice (it is strictly dominant), so it would be discovered and exploited by any depositor optimizing for reward, not just a sophisticated attacker.

### Recommendation
Align the code with the documented intent: either fix the multiplier to actually reflect a deliberate, sustainable bonus (e.g., the documented 1.5x rather than 2x) and/or restrict `_mode == 2` behind an eligibility check or cost differential comparable to the reward bonus granted, similar to the report's suggestion of unifying reward parameters or requiring a real trade-off between the two paths. Ensure the comment and implementation match to avoid divergence between intended and actual business logic.

### Proof of Concept
1. Depositor A calls `incentiveDeposit(amount, convertRatio, false, 1)` — reward computed via `getRewardAmount(amount, A, false)`, no doubling applied.
2. Depositor B calls `incentiveDeposit(amount, convertRatio, false, 2)` with the identical `amount` — reward computed via `getRewardAmount(amount, B, true)` and then doubled (`rewardToSend * 2`) before being locked via `vlMGP.lockFor`.
3. Both depositors contributed the same `amount` of WOM, but B receives 2x the MGP lock reward of A purely by selecting a different `_mode` value, with no additional capital or restriction — draining the shared MGP incentive balance twice as fast as intended for identical economic input. [1](#0-0) [3](#0-2)

### Citations

**File:** wombat/ArbWomUp3.sol (L88-105)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);

        IERC20(mgp).safeApprove(address(vlMGP), rewardToSend);
        vlMGP.lockFor(rewardToSend, msg.sender);
        // _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        emit VLMGPRewarded(msg.sender, 0, rewardToSend);
    }
```

**File:** wombat/ArbWomUp3.sol (L107-129)
```text
    function getRewardAmount(uint256 _amountToConvert, address _account, bool _lock) external view returns (uint256) {
        uint256 mgpReward = 0;

        if (!_lock) {
            mgpReward = _amountToConvert * rewardMultiplier[getUserTier(_account)] / DENOMINATOR;
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
        }

        uint256 mgpleft = IERC20(mgp).balanceOf(address(this));
        return mgpReward > mgpleft ? mgpleft : mgpReward;
    }
```

**File:** wombat/ArbWomUp3.sol (L180-212)
```text
    function _deposit(address _account, uint256 _convertRatio, uint256 _amount, uint256 _mode) internal {
        IERC20(wom).safeTransferFrom(_account, address(this), _amount);

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
        
        emit WomDeposited(_account, _amount, _mode);
    }
```
