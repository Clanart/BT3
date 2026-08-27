### Title
mWOMSVBaseRewarder never forfeits rewards for early-unlocking mWomSV holders due to dead `_calExpireForfeit` logic - ([File: wombat/WombatBribeManager.sol / rewards/mWOMSVBaseRewarder.sol])

### Summary
The premise of the question is partially incorrect: `mWOMSVBaseRewarder` has **no `queueMGP` function**, and mWomSV does not hold or vest MGP — it only locks `mWOM` and distributes whatever reward token is registered via `queueNewRewards`/`donateRewards` (not MGP). `queueMGP` exists only on `vlMGPBaseRewarder`, reached via `MasterMagpie._sendMGPForVlMGPPool`, not through mWomSV at all. However, the underlying root cause the question is pointing at — a broken/dead forfeiture calculation — is real: `mWOMSVBaseRewarder._calExpireForfeit` is dead code that always returns `forfeitAmount = 0`, unlike the analogous `vlMGPBaseRewarder._calExpireForfeit`, which correctly calls `vlMGP.getRewardablePercentWAD(_account)` to reduce payout for users mid-cooldown/early-unlock.

### Finding Description
`mWOMSVBaseRewarder._calExpireForfeit` [1](#0-0)  sets `rewardableAmount = _amount` unconditionally, so `forfeitAmount = _amount - rewardableAmount` is always `0`. This is invoked from `_sendReward` [2](#0-1) , which is called from `getReward`/`getRewards`, both gated only by `onlyMasterMagpie` [3](#0-2) , i.e. reachable by any user via `MasterMagpie`'s normal claim/harvest flow (`_claimBaseRewarder`) [4](#0-3) .

By contrast, `mWomSV.getRewardablePercentWAD` is fully implemented and correctly computes a pro-rated rewardable percentage based on locked vs. in-cooldown/unlocked amounts [5](#0-4) , mirroring `VLMGP`'s equivalent used by `vlMGPBaseRewarder._calExpireForfeit` [6](#0-5) . `mWOMSVBaseRewarder._calExpireForfeit`, however, never calls `mWOMSV.getRewardablePercentWAD(_account)` at all — the forfeiture mechanism intended to penalize early unlockers and redistribute forfeited yield to remaining long-term lockers is effectively disabled for the mWomSV reward pool.

Critically, this is **not** an MGP-vesting or `queueMGP` bug: `mWOMSVBaseRewarder` has no `queueMGP` function (only `vlMGPBaseRewarder` implements `IvlmgpPBaseRewarder.queueMGP`), and mWomSV holders lock `mWOM`, not MGP — so no "vested MGP balance" is drained through this path. The exploit sequence described in the question (lock mWOM → `unlock()` to start cooldown → call `mWOMSVBaseRewarder` harvesting mid-cooldown → receive 100% rewards with zero forfeit) is realizable, but the asset drained is whatever reward token is registered on the mWomSV rewarder (e.g., WOM emissions), not MGP.

### Impact Explanation
Theft/permanent unfair allocation of unclaimed yield: users who start `unlock()` (thus reducing `getRewardablePercentWAD` below 100%) still receive their full pro-rata share of rewards instead of a reduced share, because the forfeit amount computed by `_calExpireForfeit` is always zero. This means the pool of "forfeited" rewards meant to be redistributed to long-term lockers (via `_queueNewRewardsWithoutTransfer`) is never funded, and remaining long-term stakers receive less than they should — a real, quantifiable diversion of unclaimed yield, matching "theft or permanent freezing of unclaimed yield."

### Likelihood Explanation
Fully permissionless and trivially repeatable: any mWomSV holder can call `startUnlock`/`unlock` and then claim through normal `MasterMagpie` harvest flows with no special privileges, capital beyond owned mWOM, or complex preconditions. This is a deterministic code-logic defect (dead variable), not a probabilistic exploit — it triggers on every claim for every user, regardless of lock status.

### Recommendation
Update `mWOMSVBaseRewarder._calExpireForfeit` to call `mWOMSV.getRewardablePercentWAD(_account)` and compute `rewardableAmount = _amount * rewardablePercentWAD / 1e18`, consistent with `vlMGPBaseRewarder._calExpireForfeit`, so that early-unlocking mWomSV holders correctly forfeit the unearned share of rewards back into the pool via `_queueNewRewardsWithoutTransfer`.

### Proof of Concept
Foundry test plan:
1. Deploy `mWomSV`, `MasterMagpie`, and `mWOMSVBaseRewarder`; register a reward token and queue rewards via `queueNewRewards`.
2. User A locks mWOM and never unlocks; accrues rewards over time.
3. User B locks the same amount of mWOM, then immediately calls `startUnlock`/enters cooldown for the full balance.
4. Advance time so rewards accrue equally to both A and B's `rewardPerToken` shares (since `balanceOf` still counts cooldown amount).
5. Call harvest for both (`MasterMagpie.claim` → `mWOMSVBaseRewarder.getReward`).
6. Assert: with current code, `toSend` for User B equals `toSend` for User A (both receive 100%, `forfeitAmount == 0`), demonstrating no penalty for early unlock — expected behavior (per `getRewardablePercentWAD`) is that User B's payout should be strictly less.

Note: this PoC validates the dead-forfeiture bug in `mWOMSVBaseRewarder`, but it does not involve MGP or any `queueMGP` function, since neither exists in this contract's reachable call graph.

### Citations

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

**File:** rewards/MasterMagpie.sol (L618-629)
```text
    /// @notice Harvest reward token in BaseRewarder for an account. NOTE: Baserewarder use user staking token balance as source to
    /// calculate reward token amount
    function _claimBaseRewarder(address _stakingToken, address _account, address _receiver, address[] memory _rewardTokens) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0)) {
            if (_rewardTokens.length > 0)
                rewarder.getRewards(_account, _receiver, _rewardTokens);
            else
                // if not specifiying any reward token, just claim them all
                rewarder.getReward(_account, _receiver);
        }
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

**File:** rewards/vlMGPBaseRewarder.sol (L386-390)
```text
    function _calExpireForfeit(address _account, uint256 _amount) internal view returns (uint256) {
        uint256 rewardablePercentWAD = vlMGP.getRewardablePercentWAD(_account);
        uint256 rewardableAmount = _amount * rewardablePercentWAD / 1e18;
        if (rewardableAmount > _amount)
            revert InvalidRewardableAmount();
```
