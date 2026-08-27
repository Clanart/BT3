### Title
Unprivileged callers can force premature reward harvesting for arbitrary users via `MasterMagpie.multiclaimFor`, permanently forfeiting their unclaimed vlMGP/mWOMSV yield - (File: `rewards/MasterMagpie.sol`, `rewards/vlMGPBaseRewarder.sol`)

### Summary
`MasterMagpie.multiclaimFor` is an unrestricted `external` function that lets any caller specify an arbitrary `_account` whose rewards get harvested, unlike the analogous `depositFor`/`withdrawFor` functions which are gated by `_onlyPoolHelper`. Because harvesting through `vlMGPBaseRewarder` computes a time/lock-state-dependent forfeiture on every claim (`_calExpireForfeit`/`getRewardablePercentWAD`), an attacker can force a victim's reward checkpoint to be evaluated at an unfavorable moment (e.g., immediately after the victim starts an unlock), permanently burning part of the victim's already-accrued yield to the pool, without the victim's consent.

### Finding Description
`multiclaimFor` takes an arbitrary `_account` parameter and is callable by anyone with no ownership or delegation check: [1](#0-0) 

Compare this to `depositFor`/`withdrawFor`, which explicitly restrict the caller to the registered pool helper via `_onlyPoolHelper`: [2](#0-1) 

When `multiclaimFor` routes to `_claimBaseRewarder`, it ultimately calls `rewarder.getReward(_account, _receiver)` on the vlMGP/mWOMSV rewarder, which is restricted to `onlyMasterMagpie` — but `MasterMagpie` itself does not check that `msg.sender == _account` for the `multiclaimFor` entrypoint, so this restriction does not stop unrelated third parties from forcing a claim on behalf of any user: [3](#0-2) 

Inside `vlMGPBaseRewarder`, every reward payout goes through `_sendReward`, which computes a forfeit amount using the user's *current* rewardable percentage, not a value fixed at accrual time: [4](#0-3) 

`getRewardablePercentWAD` (in `VLMGP.sol`) is a live, time-dependent function of the user's lock/cooldown state — it changes continuously as a user progresses through an unlock schedule: [5](#0-4) 

Because the forfeiture percentage is recomputed at the moment of harvest rather than snapshotted for the user by their own choice, and because `multiclaimFor` allows *any* address to trigger that harvest for *any* victim, this mirrors the root cause of the reference bug: a value intended to reflect a specific, user-controlled point in time can instead be forced/recomputed by an unprivileged third party, producing a worse outcome (loss of value) for the victim than if only the account owner controlled when the snapshot/harvest happened.

### Impact Explanation
An attacker can grief any vlMGP/mWOMSV holder by calling `multiclaimFor` for the victim's staking token immediately after the victim opens (or during) an unlock schedule, when `getRewardablePercentWAD` is depressed. This forces `_calExpireForfeit` to burn a portion of the victim's already-earned, unclaimed reward into the pool's `queuedRewards` for redistribution to other stakers. The forfeited yield is permanently lost to the original victim — this is a direct, permanent freezing/loss of unclaimed yield for a targeted user, achievable purely through a call from an ordinary wallet with no special privileges.

### Likelihood Explanation
The attack requires only a single unprivileged transaction to `MasterMagpie.multiclaimFor`, is repeatable against any staking-token/account pair whenever the victim is in an unfavorable point of the unlock schedule, and needs no cooperation, front-running window, or special conditions beyond the victim having pending yield and an active/ recent unlock. This makes it straightforward and cheap to exploit at scale.

### Recommendation
Restrict `multiclaimFor` so it can only be called by the account itself, a registered pool helper, or an explicitly authorized operator, mirroring the `_onlyPoolHelper` restriction already used on `depositFor`/`withdrawFor`. Alternatively, ensure the vlMGP/mWOMSV rewarder rejects `getReward` calls where `msg.sender` in `MasterMagpie`'s context is not the beneficiary or an authorized delegate, so third parties cannot unilaterally trigger a victim's forfeiture-bearing harvest.

### Proof of Concept
1. Victim locks MGP in `VLMGP` and accrues vlMGP rewards over time in `vlMGPBaseRewarder`.
2. Victim calls `VLMGP.startUnlock` for part of their position, which reduces `getRewardablePercentWAD` for the still-elapsing cooldown slot per `VLMGP.getRewardablePercentWAD` [5](#0-4) .
3. Immediately after, an unrelated attacker (no special role) calls `MasterMagpie.multiclaimFor([vlMGPToken], [[]], victim)` [1](#0-0) .
4. This routes to `vlMGPBaseRewarder._sendReward`, which computes `_calExpireForfeit` using the currently-depressed `rewardablePercentWAD` and permanently transfers the forfeited amount back into the reward pool instead of to the victim [4](#0-3) .
5. The victim receives less than they would have if they alone controlled the timing of their claim, and the forfeited amount cannot be recovered by them.

### Citations

**File:** rewards/MasterMagpie.sol (L352-370)
```text
    function depositFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _deposit(_stakingToken, _for, _amount, false);
    }

    /// @notice Withdraw staking tokens from Mastser Magpie for a specific user. Can only be called by pool helper
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw   
    /// @param _for address of the user to withdraw for, and also harvested reward will be sent to
    function withdrawFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _withdraw(_stakingToken, _for, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L412-417)
```text
    /// @notice Claims for each of the pools with specified rewards to claim for each pool
    function multiclaimFor(address[] calldata _stakingTokens, address[][] memory _rewardTokens, address _account)
        external whenNotPaused
    {
        _multiClaim(_stakingTokens, _account, _account, _rewardTokens);
    }
```

**File:** rewards/MasterMagpie.sol (L601-617)
```text
    /// @notice Harvest MGP for an account
    /// only update the reward counting but not sending them to user
    function _harvestMGP(address _stakingToken, address _account) internal {
        // Harvest MGP
        uint256 pending = _calNewMGP(_stakingToken, _account);
        unClaimedMgp[_stakingToken][_account] += pending;
    }

    /// @notice calculate MGP reward based on current accMGPPerShare
    function _calNewMGP(address _stakingToken, address _account) view internal returns(uint256) {
        UserInfo storage user = userInfo[_stakingToken][_account];
        uint256 pending = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) /
            1e12 -
            user.rewardDebt;
        return pending;
    }

```

**File:** rewards/vlMGPBaseRewarder.sol (L363-400)
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

    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
    }

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

**File:** VLMGP.sol (L193-211)
```text
    function getRewardablePercentWAD(address _user) override public view returns(uint256 percent) {
        uint256 fullyInLock = getUserTotalLocked(_user);
        uint256 inCoolDown = getUserAmountInCoolDown(_user);
        uint256 userTotalVlmgp = fullyInLock + inCoolDown;
        if (userTotalVlmgp == 0)
            return 0;
        percent = fullyInLock * 1e18 / userTotalVlmgp;

        uint256 timeNow = block.timestamp;
        UserUnlocking[] storage userUnlocking = userUnlockings[_user];

        for (uint256 i; i < userUnlocking.length; i++) {
            if (userUnlocking[i].amountInCoolDown > 0) {
                if (block.timestamp > userUnlocking[i].endTime) {// fully unlocked 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 * (userUnlocking[i].endTime - userUnlocking[i].startTime)
                        / userTotalVlmgp / (timeNow - userUnlocking[i].startTime);
                }
                else {// still in cool down 
                    percent += userUnlocking[i].amountInCoolDown * 1e18 / userTotalVlmgp;
```
