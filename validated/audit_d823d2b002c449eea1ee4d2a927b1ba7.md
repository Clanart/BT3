### Title
Reward forfeiture is permanently disabled (always zero) - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`_calExpireForfeit` in `mWOMSVBaseRewarder.sol` never actually applies the intended lock-based penalty: unlike its sibling `vlMGPBaseRewarder.sol`, which computes `rewardableAmount = _amount * vlMGP.getRewardablePercentWAD(_account) / 1e18`, the mWOMSV version sets `rewardableAmount = _amount` unconditionally and never calls `mWOMSV.getRewardablePercentWAD(_account)` at all. As a result `forfeitAmount` is always `0`, so any user harvesting rewards through `getReward`/`getRewards` keeps 100% of pending rewards regardless of cooldown state.

### Finding Description
`_calExpireForfeit` is defined as: [1](#0-0) 

Compare with the correct pattern in the sibling contract, which multiplies by the decaying percent: [2](#0-1) 

In `mWOMSVBaseRewarder`, `rewardableAmount` is initialized to `_amount` and is never scaled by `mWOMSV.getRewardablePercentWAD(_account)`, so `forfeitAmount = _amount - rewardableAmount` is always `0`. This function is invoked from `_sendReward`, called from both `getReward` and `getRewards`: [3](#0-2) [4](#0-3) 

`getRewards`/`getReward` are `onlyMasterMagpie`, reachable via `MasterMagpie.multiclaimFor`/`multiclaimSpec`, which any unprivileged account can call for itself to harvest mWOMSV pool rewards. Because the forfeit computation never references the caller's lock/cooldown state, the exploit does not actually depend on "settling mid-cooldown while the percent is still near 1e18" as hypothesized in the question — it is unconditional: forfeiture never occurs under any circumstances, cooldown or fully-locked.

The question's proposed invariant break — `totalStaked()` diverging from `IERC20(mWOMSV).totalSupply()` — does not apply here, since `totalStaked()` is defined literally as `IERC20(address(mWOMSV)).totalSupply()`: [5](#0-4) 
These two values are the same storage read by construction and can never "no longer reconcile" as a consequence of this bug — that specific invariant claim in the question is not meaningful for this contract.

### Impact Explanation
Because forfeiture is unconditionally zero, users lose no yield for exiting early, meaning the penalty mechanism intended to discourage early exit / reward the fully-committed lockers is completely inert. This transfers value from long-term lockers (who should receive forfeited rewards redistributed via `_queueNewRewardsWithoutTransfer`) to any withdrawing user — a form of unclaimed-yield misallocation, consistent with a "Theft of unclaimed yield" class of impact, though it is a permanent code defect rather than a precisely-timed race exploiting decay-window boundaries as described in the question.

### Likelihood Explanation
Trivially reachable: any staker calling `getReward`/`getRewards` (via `MasterMagpie.multiclaimFor`/`multiclaimSpec`) never forfeits rewards, with no special timing, capital, or crafted reward-token array needed — it happens on every claim, always. No preconditions about "the account's slot matured recently" are required, since the percent-scaling code path is never reached at all.

### Recommendation
Add the missing scaling in `_calExpireForfeit` in `mWOMSVBaseRewarder.sol` to mirror `vlMGPBaseRewarder.sol`:
```solidity
uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
```
so that the forfeit is actually computed from the account's current lock/cooldown state.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`; lock mWOM for a user, queue rewards via `queueNewRewards`.
2. Call `startUnlock` for a portion of the user's balance to begin cooldown (`getRewardablePercentWAD` decays over time toward the locked fraction).
3. Advance time partway through cooldown (e.g., 50%), call `earned`/`calExpireForfeit(user, rewardToken)` and assert it returns `0` regardless of elapsed cooldown time or `getRewardablePercentWAD(user)` value — demonstrating `_calExpireForfeit` never varies with lock state, contradicting the intended decayed-forfeit design (compare against `vlMGPBaseRewarder.calExpireForfeit` behavior under equivalent lock/cooldown setup, which does return non-zero forfeit).
4. Have the user call `getRewards`/`getReward` (through `MasterMagpie.multiclaimFor`) and assert `toSend == userRewards` (full amount) and `forfeitAmount == 0` for every reward token, in every cooldown state tested (0%, 25%, 50%, 99% elapsed, and fully matured).

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L138-140)
```text
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(mWOMSV)).totalSupply();
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L249-261)
```text
    function getRewards(address _account, address _receiver, address[] memory _rewardTokens)
        public
        onlyMasterMagpie
        updateRewards(_account, _rewardTokens)
        nonReentrant
    {
        uint256 length = _rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L362-376)
```text
    function _sendReward(address _rewardToken, address _account, address _receiver) internal {
        uint256 forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account]);
        uint256 toSend = userRewards[_rewardToken][_account] - forfeitAmount;


        userRewards[_rewardToken][_account] = 0;
            
        if (toSend > 0) {
            IERC20(_rewardToken).safeTransfer(_receiver, toSend);
            emit RewardPaid(_account, _receiver, toSend, _rewardToken);
        }

        if(forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L385-398)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardableAmount = _amount;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
        
        if (forfeitAmount < (_amount / 1000)) {  // if forfeitAmount is smaller than 0.1% ignore to save gas fee
            forfeitAmount = 0;
            rewardableAmount = _amount;
        }

        return forfeitAmount;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L386-400)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();

        uint256 forfeitAmount = _amount - rewardableAmount;
        
        if (forfeitAmount < (_amount / 1000)) {  // if forfeitAmount is smaller than 0.1% ignore to save gas fee
            forfeitAmount = 0;
            rewardableAmount = _amount;
        }

        return forfeitAmount;
    }
```
