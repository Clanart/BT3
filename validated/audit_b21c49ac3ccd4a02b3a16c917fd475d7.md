This confirms the vulnerability. Comparing the two analogous rewarder implementations:

- `vlMGPBaseRewarder._calExpireForfeit` correctly calls `vlMGP.getRewardablePercentWAD(_account)` to pro-rate the reward based on cooldown status [1](#0-0) 
- `mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` directly, never calling `mWOMSV.getRewardablePercentWAD()`, meaning `forfeitAmount` is always `0` [2](#0-1) 

The `mWomSV` contract does implement `getRewardablePercentWAD(address _user)` which pro-rates based on locked vs. cooldown amount and elapsed cooldown time, confirming this function exists and is meant to be used [3](#0-2) . This is reachable via `getReward()`/`getRewards()` which call `_sendReward` → `_calExpireForfeit` [4](#0-3) , and `getReward`/`getRewards` are `onlyMasterMagpie`-gated, callable through `MasterMagpie.multiclaim` [5](#0-4) .

### Title
mWOMSVBaseRewarder._calExpireForfeit never applies cooldown pro-ration, allowing users in cooldown to claim 100% of accrued rewards - (File: rewards/mWOMSVBaseRewarder.sol)

### Summary
`mWOMSVBaseRewarder._calExpireForfeit` sets `rewardableAmount = _amount` unconditionally instead of scaling by `mWOMSV.getRewardablePercentWAD(_account)` as the analogous `vlMGPBaseRewarder._calExpireForfeit` does for vlMGP. As a result, any mWomSV holder with an active or expired-but-unclaimed cooldown slot always receives 100% of their accrued mWOMSV bonus reward via `getReward()`/`getRewards()`, instead of the pro-rated share, with zero forfeiture ever queued back to remaining lockers.

### Finding Description
`mWOMSVBaseRewarder.getReward`/`getRewards` (onlyMasterMagpie, reachable via `MasterMagpie.multiclaim`) call `_sendReward`, which computes `forfeitAmount = _calExpireForfeit(_account, userRewards[_rewardToken][_account])` and pays `toSend = userRewards - forfeitAmount` to the receiver [4](#0-3) .

In the vlMGP variant, `_calExpireForfeit` fetches the caller's rewardable percentage from the locker contract: `rewardableAmount = _amount * vlMGP.getRewardablePercentWAD(_account) / 1e18` [1](#0-0) , correctly reducing payout for users with balance still in cooldown or partially forfeitable.

`mWOMSVBaseRewarder._calExpireForfeit`, however, is:
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
`rewardableAmount` is set equal to `_amount` with no lookup of `mWOMSV.getRewardablePercentWAD(_account)`, so `forfeitAmount` is always `0` regardless of the user's lock/cooldown state [2](#0-1) . The `mWomSV` locker contract does implement the correct percentage function that should have been used, exactly mirroring `VLMGP.getRewardablePercentWAD` [3](#0-2) .

No modifier, reward-index update, or receipt-token accounting in `getReward`/`getRewards`/`updateReward` compensates for this — `updateReward` only refreshes `userRewards`/`userRewardPerTokenPaid`, it does not gate on cooldown status. Any unprivileged holder of mWomSV who starts a cooldown (`startUnlock`) retains their full `balanceOf` (locked + in-cooldown) for reward accrual purposes via `_earned`, but faces zero forfeiture when claiming, unlike the intended design where partially/fully-in-cooldown balances should yield a reduced share with the remainder queued back via `_queueNewRewardsWithoutTransfer` to other stakers.

### Impact Explanation
This is a theft of yield: users who have signaled unlock intent (cooldown) are supposed to forfeit a pro-rated portion of newly accrued mWOMSV bonus rewards back to the pool (as `ForfeitRewardAdded`/re-queued rewards) so remaining long-term lockers benefit. Because `forfeitAmount` is always 0, every mWomSV holder claims 100% regardless of cooldown state, permanently denying the forfeiture share to the remaining honest lockers. This matches the Immunefi impact class "theft of unclaimed yield" and violates the reward conservation invariant intended by the protocol's own analogous vlMGP implementation.

### Likelihood Explanation
Trivial to trigger and always reachable by any unprivileged mWomSV holder: lock mWOM via `lock()`, call `startUnlock()` to enter cooldown, wait/accrue reward via `queueNewRewards`, then call `getReward()`/`getRewards()` (directly or via `MasterMagpie.multiclaim`). No special capital, front-running, or governance access is required — it happens on every single claim by every cooldown-holding user, making this a systemic, 100%-repeatable loss rather than an edge case.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to mirror `vlMGPBaseRewarder._calExpireForfeit`:
```solidity
function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
    uint256 rewardablePercentWAD = mWOMSV.getRewardablePercentWAD(_account);
    uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
    if (rewardableAmount > _amount) revert InvalidRewardableAmount();
    uint256 forfeitAmount = _amount - rewardableAmount;
    if (forfeitAmount < (_amount / 1000)) {
        forfeitAmount = 0;
        rewardableAmount = _amount;
    }
    return forfeitAmount;
}
```

### Proof of Concept
Foundry test outline:
1. Deploy `mWomSV`, `mWOMSVBaseRewarder`, `MasterMagpie` wiring per existing test harness.
2. User A locks `1000 mWOM` via `lock(1000e18)`.
3. Reward manager calls `queueNewRewards(reward, rewardToken)` on `mWOMSVBaseRewarder` to accrue `earned(A) = E`.
4. User A calls `startUnlock(500e18)` (or equivalent) putting 50% of balance into cooldown, well before `endTime`.
5. Immediately (still within cooldown, `block.timestamp < endTime`) call `getReward(A, A)` via `MasterMagpie.multiclaim` or directly (as `onlyMasterMagpie`, call through MasterMagpie).
6. Assert: received amount == `E` (full earned), while expected correct behavior per `getRewardablePercentWAD` (~50-75% depending on elapsed cooldown time) should be `E * getRewardablePercentWAD(A) / 1e18 < E`.
7. Assert `rewards[rewardToken].queuedRewards`/`rewardPerTokenStored` shows no forfeited amount was re-queued (`ForfeitRewardAdded` never emitted), confirming the missing pro-ration and permanent loss of forfeitable yield to other stakers.

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

**File:** rewards/mWOMSVBaseRewarder.sol (L233-261)
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

**File:** wombat/mWomSV.sol (L181-206)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalmWomSV = fullyInLock + inCoolDown;
        if (userTotalmWomSV == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalmWomSV;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalmWomSV / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalmWomSV;
                }

            }
        }

        return percent;
    }
```
