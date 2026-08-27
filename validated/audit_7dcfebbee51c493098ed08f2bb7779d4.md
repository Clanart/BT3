### Title
vlMGP reward forfeiture can be bypassed via claim-splitting, diverting forfeited yield away from properly-locked stakers - (File: rewards/vlMGPBaseRewarder.sol)

### Summary
`vlMGPBaseRewarder` is meant to penalize users who have partially exited their `vlMGP` lock (i.e., have tokens sitting in cooldown) by forfeiting a proportional share of their earned rewards, which is then redistributed to fully-locked holders. The forfeiture calculation contains a dust-forgiveness rule that ignores forfeiture entirely whenever the computed `forfeitAmount` is smaller than `_amount / 1000`. Because this threshold is evaluated per-claim on the *incremental* earned amount rather than on the user's cumulative position, an ordinary wallet can repeatedly call the permissionless claim path with small enough increments to keep every single `forfeitAmount` under the 0.1% dust threshold, permanently avoiding the penalty regardless of how large their cooldown balance is.

### Finding Description
`_calExpireForfeit()` computes the portion of a reward amount that should be forfeited based on `vlMGP.getRewardablePercentWAD(_account)`, which reflects the fraction of a user's `vlMGP` still fully locked versus in cooldown/unlocking: [1](#0-0) 

The dust check at line 394 (`if (forfeitAmount < (_amount / 1000))`) is intended only to save gas on negligible amounts, but it is applied to whatever `_amount` happens to be passed in — which is the user's currently-accrued, unclaimed reward at the time of the call, not their total historical earnings. This function is invoked from `_sendReward()`, which is reachable by any unprivileged holder via `getReward()`/`getRewards()` (gated only by `onlyMasterMagpie`, i.e., routed through the user-facing claim path in `MasterMagpie`): [2](#0-1) [3](#0-2) 

Because `updateReward`/`updateRewards` snapshot and reset `userRewards[...]` to the earned-since-last-checkpoint amount on every claim call (see `_updateFor`/`updateRewards` modifiers), a user can call `getReward` immediately after each small increase in `rewardPerToken` (each time the reward manager queues new rewards) instead of waiting to accumulate a large balance: [4](#0-3) [5](#0-4) 

By claiming in many small increments (analogous to "salami slicing"), each individual `_amount` passed into `_calExpireForfeit` is small enough that `forfeitAmount < _amount/1000` is satisfied on essentially every call, so `forfeitAmount` is zeroed and `rewardableAmount` is treated as the *entire* small chunk — even though the user has a large, real cooldown/unlocking balance that should be causing a proportional, non-trivial forfeiture. This is structurally the same bug class as the WiseLending report: a size-based safety/penalty check that is meaningful in aggregate is trivially bypassed by fragmenting a position/action into sub-threshold pieces, because the check only ever looks at each isolated action in isolation rather than the user's true cumulative state.

### Impact Explanation
Forfeited amounts are not burned; they are redistributed to remaining stakers via `_queueNewRewardsWithoutTransfer`, which increases `rewardPerTokenStored` for everyone still holding `vlMGP`: [6](#0-5) 

A user who exploits claim-splitting to zero out their forfeiture keeps yield that the protocol's own accounting says they are not entitled to (because they no longer have their MGP fully locked), while other, fully-locked `vlMGP` holders are permanently deprived of the redistributed share they would otherwise have received. This is a direct theft of unclaimed yield from honest, properly-locked stakers, redirected to a partially-unlocked staker who should be penalized.

### Likelihood Explanation
The exploit requires no privileged role, no oracle manipulation, and no interaction with governance — only repeated calls to a normal, permissionless claim function (`getReward`) timed around each reward-queuing event, which is easily automatable by any wallet holding `vlMGP` with an active `startUnlock` cooldown. The economic incentive scales with the size of the user's cooldown position and the frequency of reward queuing, making this practically exploitable rather than theoretical.

### Recommendation
Evaluate the forfeiture dust threshold against the user's cumulative unforfeited/earned rewards (or accumulate small forfeitable dust across claims) rather than against each individual, attacker-controlled claim increment, e.g., track a running forfeit-eligible balance per user and only waive the dust-sized remainder once, or compute forfeiture based on the user's full outstanding earned balance prior to any claim-splitting rather than the per-call delta.

### Proof of Concept
1. User locks MGP into `vlMGP`, then calls `startUnlock` for a large portion (e.g., 90%) of their position, leaving `getRewardablePercentWAD` low (heavily discounted). [7](#0-6) 
2. The reward manager periodically calls `queueNewRewards`, incrementing `rewardPerTokenStored` for the pool in small steps. [8](#0-7) 
3. Immediately after each such increment, the user calls `getReward` (via `MasterMagpie`), so that `userRewards[_rewardToken][_account]` computed in `_earned` at claim time is always small. [9](#0-8) 
4. In `_sendReward` -> `_calExpireForfeit`, since each claimed `_amount` is small, `forfeitAmount < _amount/1000` holds every time, so `forfeitAmount` is set to 0 and the user receives 100% of each increment despite their low `rewardablePercentWAD`. [1](#0-0) 
5. Repeating this over the cooldown period, the user accumulates significantly more reward tokens than the protocol's forfeiture design intended, at the expense of fully-locked `vlMGP` holders who would have received those forfeited amounts.

**Note on verification limits:** I was unable to locate the exact `MasterMagpie` claim entrypoint (`multiclaim`/`getReward` wrapper) in the indexed contents to confirm the exact call signature and any rate limiting, since `MasterMagpie.sol` did not return matches for the relevant function names in the available index — this may be due to index coverage limits rather than the function's absence. If a precise confirmation of the outer claim function is needed, a Devin session with full repository access should be used to inspect `MasterMagpie.sol` directly.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L100-118)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userVlMGPAmount = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userVlMGPAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
    }

    modifier updateReward(address _account) {
        _updateFor(_account);
        _;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L232-260)
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

**File:** rewards/vlMGPBaseRewarder.sol (L294-327)
```text
    function queueNewRewards(uint256 _amountReward, address _rewardToken)
        override
        external
        onlyManager
        returns (bool)
    {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }
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
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
        return true;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L331-347)
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
                (_amountReward * 10**vlMGPDecimal) / totalStaked();
        }
        emit ForfeitRewardAdded(_amountReward, _rewardToken);
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L349-361)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        uint256 userVlMGPAmount = balanceOf(_account);

        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;

            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userVlMGPAmount);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L363-377)
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

**File:** rewards/vlMGPBaseRewarder.sol (L379-384)
```text
    function _earned(address _account, address _rewardToken, uint256 _userVlmgpShare) internal view returns (uint256) {
        return ((_userVlmgpShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**vlMGPDecimal) + userRewards[_rewardToken][_account];
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

**File:** VLMGP.sol (L275-311)
```text
    function startUnlock(uint256 _amountToCoolDown) external override whenNotPaused nonReentrant {
        if (_amountToCoolDown > getUserTotalLocked(msg.sender))
            revert NotEnoughLockedMPG();

        uint256 totalLockAfterStartUnlock = getUserTotalLocked(msg.sender) - _amountToCoolDown;
        if (address(wombatBribeManager) != address(0) && 
            totalLockAfterStartUnlock < IWombatBribeManager(wombatBribeManager).userTotalVotedInVlmgp(msg.sender))
            revert NotEnoughLockedMPG();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 _slotIndex = getNextAvailableUnlockSlot(msg.sender);
        totalAmountInCoolDown += _amountToCoolDown;

        if (_slotIndex < getUserUnlockSlotLength(msg.sender)) {
            userUnlockings[msg.sender][_slotIndex] = UserUnlocking({
                startTime: block.timestamp,
                endTime: block.timestamp + coolDownInSecs,
                amountInCoolDown: _amountToCoolDown
            });
        } else {
            userUnlockings[msg.sender].push(
                UserUnlocking({
                    startTime: block.timestamp,
                    endTime: block.timestamp + coolDownInSecs,
                    amountInCoolDown: _amountToCoolDown
                })
            );
        }

        if (referralStorage != address(0)) IReferralStorage(referralStorage).updateTotalFactor(msg.sender);

        emit UnlockStarts(msg.sender, block.timestamp, _amountToCoolDown);
    }
```
