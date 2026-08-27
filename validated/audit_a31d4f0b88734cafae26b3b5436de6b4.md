### Title
mWOMSVBaseRewarder never applies the expire-forfeit penalty, permanently denying stakers their entitled forfeited yield - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` is dead code that always returns `0`, so no reward is ever forfeited when a mWOMSV holder claims. This mirrors the analog bug class ("quorum" check silently becoming ineffective/zero and defeating the intended pass/fail logic): here the forfeiture "gate" is unconditionally short-circuited to zero, so users who should be penalized for withdrawing/claiming outside the vesting rule keep 100% of their rewards, while the rest of the pool permanently loses the yield that should have been redistributed to them.

### Finding Description
`vlMGPBaseRewarder._calExpireForfeit` correctly computes a rewardable percentage from the locker and forfeits the remainder: [1](#0-0) 

`mWOMSVBaseRewarder._calExpireForfeit`, which is supposed to be the analogous check for mWOMSV holders, never queries any vesting/lock state. It sets `rewardableAmount = _amount` unconditionally, making `forfeitAmount = _amount - rewardableAmount` always `0`: [2](#0-1) 

This function is invoked on every claim path (`getReward`/`getRewards` → `_sendReward` → `_calExpireForfeit`), which is reachable by any ordinary mWOMSV holder — no privileged role is required: [3](#0-2) [4](#0-3) 

The `ILocker` interface used to type `mWOMSV` in this contract only exposes `lockFor`, and does not declare `getRewardablePercentWAD`, even though the actual `mWomSV` contract implements such a function (as used by the parallel `vlMGP`/`vlMGPBaseRewarder` pattern): [5](#0-4) 

Because the intended forfeit percentage lookup was never wired in, `_calExpireForfeit` degenerates into `rewardableAmount = _amount; forfeitAmount = 0`, exactly the "threshold always evaluates to zero" failure mode described in the report — the check exists syntactically but has no effect on the outcome.

### Impact Explanation
The forfeit mechanism exists to slash a portion of rewards for users who claim/withdraw outside the intended vesting terms and redistribute that forfeited amount as additional yield to the remaining stakers via `_queueNewRewardsWithoutTransfer`: [6](#0-5) 

With forfeiture permanently disabled, every mWOMSV holder always receives their full reward regardless of vesting status, and the pool of remaining/long-term stakers permanently loses the yield they were entitled to receive from other users' forfeitures. This is a permanent, protocol-wide freezing/loss of yield for honest stakers — funds that should accrue to the remaining stakers as forfeited yield never materialize, for as long as the contract is in use.

### Likelihood Explanation
This triggers on every single claim by any ordinary wallet holding mWOMSV rewards — no special conditions, timing, or privileged access are needed. It is deterministic dead code, so it fires 100% of the time the claim path is used, making likelihood effectively certain.

### Recommendation
Wire `mWOMSV`'s actual rewardable-percent lookup (analogous to `vlMGP.getRewardablePercentWAD(_account)`) into `_calExpireForfeit`, and extend `ILocker` (or introduce a dedicated interface) to expose `getRewardablePercentWAD` so `mWOMSVBaseRewarder` can call it, matching the pattern already used in `vlMGPBaseRewarder`.

### Proof of Concept
1. A user stakes mWOMSV and accrues rewards; rewards for the pool are queued via `queueNewRewards`/`donateRewards`, updating `rewardPerTokenStored`. [7](#0-6) 
2. The user calls `getReward` (via MasterMagpie) before whatever vesting condition should apply for full rewardability. [3](#0-2) 
3. `_sendReward` calls `_calExpireForfeit(_account, userRewards[...])`, which always returns `0` regardless of the account's actual vesting/lock state, since `rewardableAmount` is hardcoded to equal `_amount`. [4](#0-3) 
4. The full reward amount is transferred to the user with no forfeiture ever queued back into `rewardPerTokenStored` for other stakers, confirming the forfeit mechanism has no effect for any account or amount.

### Citations

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

**File:** rewards/mWOMSVBaseRewarder.sol (L233-247)
```text
    function getReward(address _account, address _receiver)
        public
        onlyMasterMagpie
        updateReward(_account)
        returns (bool)
    {
        uint256 length = rewardTokens.length;

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            _sendReward(rewardToken, _account, _receiver);
        }

        return true;
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-328)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amountReward
        );
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;

        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L330-346)
```text
    function _queueNewRewardsWithoutTransfer(uint256 _amountReward, address _rewardToken) internal
    {
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards = rewardInfo.historicalRewards + _amountReward;
        if (totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
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

**File:** interfaces/ILocker.sol (L1-6)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

interface ILocker {
    function lockFor(uint256 _amount, address _for) external;
}
```
