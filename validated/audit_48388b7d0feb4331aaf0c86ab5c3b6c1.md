### Title
Broken forfeiture calculation permanently forfeits yield redistribution in `mWOMSVBaseRewarder::_calExpireForfeit` - (File: `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`mWOMSVBaseRewarder::_calExpireForfeit` is supposed to reduce a claimant's reward by a "forfeit" factor (mirroring the pattern used in the sibling contract `vlMGPBaseRewarder`) and redistribute the forfeited portion back to the pool for other stakers. In `mWOMSVBaseRewarder` this scaling/percentage step is missing entirely, so the function is a permanent no-op: it always returns `forfeitAmount == 0`, meaning every claimant always receives 100% of accrued rewards regardless of their actual lock/cooldown state, and the forfeiture redistribution to other mWomSV lockers never happens.

### Finding Description
`vlMGPBaseRewarder::_calExpireForfeit` (lines 386-400) correctly derives the rewardable share from an external scaling factor before computing the forfeit: [1](#0-0) 

```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
}
```

The equivalent function in `mWOMSVBaseRewarder` (lines 385-398) drops the scaling-factor multiplication (`* rewardablePercentWAD / 1e18`) and instead assigns `rewardableAmount = _amount` directly: [2](#0-1) 

```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardableAmount = _amount;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
    return forfeitAmount;
}
```

Because `rewardableAmount` is always set equal to `_amount`, `forfeitAmount` is mathematically always `0` for every account, every call, regardless of the actual lock or cooldown status of the mWomSV position. This is the same bug class as the reported RAAC issue: a scaling/adjustment factor that a downstream consumer (`_sendReward`) depends on is silently dropped in one code path while it is correctly applied in the parallel/sibling implementation.

This is reachable from any ordinary wallet holding mWomSV lock positions: `getReward` / `getRewards` (both callable via `MasterMagpie`, which any user interacts with to claim rewards) invoke `_sendReward`, which calls `_calExpireForfeit`: [3](#0-2) 

### Impact Explanation
The forfeiture mechanism exists specifically to claw back yield from lockers who should not be fully "rewardable" (e.g., due to early/expired lock states) and requeue that forfeited amount back into the pool via `_queueNewRewardsWithoutTransfer` for redistribution to other, legitimately-rewardable mWomSV lockers: [4](#0-3) 

Since `forfeitAmount` is always `0`, this redistribution never occurs. Every claimant permanently receives their full accrued reward whether or not they qualify, and other honest lockers who are supposed to receive that forfeited yield share via `rewardPerTokenStored` top-ups permanently lose access to it. This is a permanent freezing/loss of unclaimed yield for the pool's other stakers — the yield disappears from the intended distribution path entirely (paid out instead of clawed back and redistributed), matching the accepted "theft or permanent freezing of unclaimed yield" impact category.

### Likelihood Explanation
High likelihood: any account with an mWOMSV lock position calling `getReward`/`getRewards` (a routine, unprivileged, frequent user action) triggers this broken logic unconditionally on every claim. No special preconditions, timing, or privileged role is required.

### Recommendation
Mirror the correct pattern from `vlMGPBaseRewarder`: fetch the actual rewardable percentage/factor from `mWOMSV` (e.g. an equivalent `getRewardablePercentWAD`/expiry-aware accessor exposed by the `ILocker` interface implemented by the mWomSV lock contract) and apply it before comparing/subtracting:

```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    if (rewardableAmount > _amount)
        revert InvalidRewardableAmount();

    uint256 forfeitAmount = _amount - rewardableAmount;
    ...
}
```

### Proof of Concept
1. A user locks mWOM into an mWomSV position that is expired/eligible for forfeiture per the lock contract's rules.
2. Rewards accrue in `mWOMSVBaseRewarder` via `_provisionReward`/`rewardPerTokenStored` as usual.
3. The user calls `getReward` (via `MasterMagpie`), which is `onlyMasterMagpie`-gated but reachable by any ordinary wallet through the standard claim flow, invoking `_sendReward` → `_calExpireForfeit(_account, userRewards[_rewardToken][_account])`. [5](#0-4) 
4. Inside `_calExpireForfeit`, `rewardableAmount` is set equal to `_amount`, so `forfeitAmount = _amount - rewardableAmount` evaluates to `0` unconditionally — the check for lock/expiry status that should reduce the payout is never applied.
5. `_sendReward` transfers the full `_amount` to the user and skips `_queueNewRewardsWithoutTransfer` (since `forfeitAmount == 0`), so no yield is ever redirected back to the pool for other stakers, even in cases the design intends forfeiture to occur.

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
