### Title
`_calExpireForfeit` in mWOMSVBaseRewarder never applies lock-based forfeiture, letting cooling-down/unlocked stakers keep 100% of rewards - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally and never queries the locker's rewardable percentage, so `forfeitAmount` is always `0` regardless of whether the account's mWOMSV is fully unlocked/in cooldown. This is confirmed by direct comparison with the sibling contract `vlMGPBaseRewarder.sol`, whose `_calExpireForfeit` correctly calls `vlMGP.getRewardablePercentWAD(_account)` to scale `rewardableAmount`, while the mWOMSV version omits this call entirely.

### Finding Description
In `rewards/mWOMSVBaseRewarder.sol`, `_calExpireForfeit` is implemented as: [1](#0-0) 

`rewardableAmount` is initialized to `_amount` and is never reduced by any lock/cooldown factor before the forfeit calculation `forfeitAmount = _amount - rewardableAmount`, which is therefore always `0`. Compare this to the equivalent function in `rewards/vlMGPBaseRewarder.sol`: [2](#0-1) 

which multiplies `_amount` by `vlMGP.getRewardablePercentWAD(_account)` before comparing/subtracting — the exact mechanism `mWOMSVBaseRewarder` is missing.

This function is invoked from `_sendReward`, called by `getReward`/`getRewards`: [3](#0-2) [4](#0-3) 

`getRewards` is gated only by `onlyMasterMagpie`, meaning any regular user calls it indirectly through MasterMagpie's public claim path — no privileged role is required. Because `toSend = userRewards[...] - forfeitAmount` and `forfeitAmount` is always `0`, users in cooldown/unlock still receive their entire accrued reward via `IERC20.safeTransfer`, and `_queueNewRewardsWithoutTransfer` (the forfeiture redistribution path to remaining full lockers) is never triggered since `forfeitAmount > 0` is never true.

Existing guards (`onlyMasterMagpie`, `nonReentrant`, `updateRewards`) do not address this — they gate access and reentrancy but don't perform the forfeiture calculation themselves; that logic is entirely delegated to the broken `_calExpireForfeit`.

### Impact Explanation
This matches "theft or permanent freezing of unclaimed yield": the protocol design intends users who are not fully locked (in cooldown/unlocked) to forfeit a portion of rewards proportional to their non-locked share, redistributing the forfeited amount to accounts that remain fully locked. Because the dead branch never computes a nonzero forfeit, every mWOMSV staker (attacker or not) retains 100% of rewards even at 0% `rewardablePercentWAD`, permanently denying the redistribution that fully-locked stakers were economically entitled to. This is a systemic yield-misallocation affecting all reward tokens routed through this rewarder, not merely a theoretical edge case.

### Likelihood Explanation
No special capital or privilege is needed: any staker can call `mWOMSV.unlock()` on their full balance to enter cooldown, wait for `queueNewRewards` to accrue `rewardPerTokenStored`, then call `getRewards` through the normal MasterMagpie claim flow. This is 100% reproducible on every call since the bug is unconditional (not probabilistic), and requires no reentrancy, flash loans, or governance/admin rights.

### Recommendation
Fix `_calExpireForfeit` in `rewards/mWOMSVBaseRewarder.sol` to mirror `vlMGPBaseRewarder.sol`: query the locker for the account's rewardable percentage (e.g., `mWOMSV.getRewardablePercentWAD(_account)`, adding this method to `ILocker`/`mWomSV.sol` if not already exposed) and scale `rewardableAmount = _amount * rewardablePercentWAD / 1e18` before computing `forfeitAmount`.

### Proof of Concept
Hardhat test outline:
1. Deploy/mock `mWOMSV`, `MasterMagpie`, `mWOMSVBaseRewarder`, and a reward token; register reward token via `queueNewRewards`.
2. Have attacker stake mWOMSV (or acquire mWOMSV balance) then call `mWomSV.unlock(fullAmount)` to move 100% into cooldown (rewardable percent should be 0% per intended design, verified via `mWOMSV.getRewardablePercentWAD`/equivalent if exposed, or by checking cooldown state).
3. Have `rewardManager` call `queueNewRewards(amount, rewardToken)` to accrue `rewardPerTokenStored`.
4. Assert `earned(attacker, rewardToken) > 0` and `calExpireForfeit(attacker, rewardToken) == 0` despite being fully in cooldown.
5. Call `getRewards(attacker, attacker, [rewardToken])` via MasterMagpie and assert the full `earned` amount is transferred (`RewardPaid` event amount == pre-call `earned` value), and `rewards[rewardToken].queuedRewards`/`historicalRewards` do not increase from any forfeiture (no `ForfeitRewardAdded` event emitted), confirming zero funds are redirected to fully-locked stakers.

### Citations

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
