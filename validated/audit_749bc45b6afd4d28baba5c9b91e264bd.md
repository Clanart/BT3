### Title
Zero-supply reward-queuing window in `vlMGPBaseRewarder` lets a re-locker capture disproportionate forfeited yield - ([File: rewards/vlMGPBaseRewarder.sol])

### Summary
`_queueNewRewardsWithoutTransfer` and `queueNewRewards` in `rewards/vlMGPBaseRewarder.sol` check `totalStaked() == 0` and, if true, park the incoming reward/forfeit amount in `rewardInfo.queuedRewards` instead of updating `rewardPerTokenStored`. When `totalStaked()` (i.e. `vlMGP.totalSupply()`) later becomes nonzero again, the *entire* queued amount is flushed into `rewardPerTokenStored` divided by whatever `totalStaked()` is at that exact moment, regardless of how long that new balance has been staked.

### Finding Description
`totalStaked()` returns `vlMGP.totalSupply()` [1](#0-0) . `_queueNewRewardsWithoutTransfer`, called from `_sendReward` on any forfeited (expired/penalized) reward amount, and from `queueMGP` on forfeited MGP, defers accounting into `queuedRewards` whenever `totalStaked()==0`, and otherwise dumps the full queued balance plus the new reward into `rewardPerTokenStored` scaled by the current `totalStaked()` [2](#0-1) . The same zero-supply-queuing pattern also exists in `queueNewRewards` [3](#0-2)  and in `queueMGP`'s forfeit routing [4](#0-3) .

Given the stipulated precondition that an attacker is the sole/last locker able to drive `vlMGP.totalSupply()` to exactly 0 (via `unlock`/`forceUnLock` in `VLMGP.sol`, both of which reduce `totalAmount` through `_unlock`) [5](#0-4) , any forfeit or reward-queue event that lands while supply is 0 accumulates in `queuedRewards` with no dilution. If the attacker then re-locks a minimal amount (`lock(1)`), they become the sole staked balance holder. The next event that calls `_queueNewRewardsWithoutTransfer` or `queueNewRewards` with nonzero `totalStaked()` will divide the *entire* accumulated `queuedRewards` by the attacker's tiny balance, producing an outsized `rewardPerTokenStored` increment that the attacker alone can claim via `getReward`/`_sendReward` [6](#0-5) .

No modifier or check prevents this: `lock`/`lockFor` only require `whenNotPaused`/`nonReentrant` [7](#0-6) , and there is no time-weighting, minimum-duration, or pro-rata distribution mechanism for rewards accrued while supply was zero — the flush is instantaneous and winner-take-all for whoever is staked at that instant.

### Impact Explanation
This allows a capital-light attacker (locking as little as 1 wei of MGP) to capture yield that was forfeited by/owed to the broader pool of lockers, rather than having it distributed proportionally once real stakers return. This is theft of forfeited/queued yield belonging to other participants, matching the "theft of unclaimed/forfeited yield" impact class.

### Likelihood Explanation
Likelihood is low-to-moderate and highly conditional: it requires `vlMGP.totalSupply()` to hit exactly 0, meaning the attacker (or colluding parties) must control effectively the entire locked supply at that moment — an unrealistic condition once the pool has meaningful, diversified TVL, but plausible in a nascent/low-TVL pool or a pool that has been mostly drained. It also requires precise mempool-watching/timing to re-lock immediately after a forfeit-triggering harvest lands while supply is still 0, and before any other party locks. The bug is real and reproducible under these conditions but the "sole staker able to zero total supply" precondition substantially limits real-world exploitability at scale.

### Recommendation
Do not allow a single, disproportionately small re-locker to claim the entirety of rewards queued during a zero-supply period. Options: (1) time-weight/vest the flush of `queuedRewards` instead of applying it in a single instant division by current `totalStaked()`; (2) require a minimum elapsed duration or minimum stake before newly locked balances become eligible for pre-existing `queuedRewards`; (3) snapshot/checkpoint `queuedRewards` distribution based on stake-time integrals rather than point-in-time `totalStaked()`.

### Proof of Concept
Foundry test plan:
1. Deploy `VLMGP`, `MasterMagpie`, and `vlMGPBaseRewarder` with a reward token.
2. Have User A lock all outstanding MGP so `vlMGP.totalSupply() == User A's balance`.
3. Have User A fully `unlock`/`forceUnLock` their position so `vlMGP.totalSupply() == 0`.
4. Trigger a call path that invokes `_queueNewRewardsWithoutTransfer` (e.g., a manager calling `queueMGP` with a forfeit, or another pending forfeit flow) while `totalStaked()==0`; assert `rewardInfo.queuedRewards` increases and `rewardPerTokenStored` stays unchanged.
5. Have attacker call `lock(1)` (1 wei of MGP) so `totalStaked() == 1`.
6. Trigger another `queueNewRewards`/`_queueNewRewardsWithoutTransfer` call with a small new amount; assert `rewardPerTokenStored` jumps by `(queuedRewards + newAmount) * 10**18 / 1`.
7. Have attacker call `getReward` and assert `earned(attacker, rewardToken)` (and the actual token transfer) is approximately equal to the entire previously queued amount, vastly exceeding the attacker's 1-wei contribution — confirming disproportionate capture.

### Citations

**File:** rewards/vlMGPBaseRewarder.sol (L137-139)
```text
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(vlMGP)).totalSupply();
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L274-289)
```text
    function queueMGP(uint256 _amount, address _account, address _receiver) override external onlyManager nonReentrant returns (bool) {
        IERC20(vlMGP.MGP()).safeTransferFrom(msg.sender, address(this), _amount);
        
        uint256 forfeitAmount = _calExpireForfeit(_account, _amount);
        uint256 rewardableAmount = _amount - forfeitAmount;
        
        if (forfeitAmount > 0)
            _queueNewRewardsWithoutTransfer(forfeitAmount, address(vlMGP.MGP()));

        if (rewardableAmount > 0) {
            IERC20(vlMGP.MGP()).safeTransfer(_receiver, rewardableAmount);
            emit MGPHarvested(_account, rewardableAmount, forfeitAmount);
        }

        return true;
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L309-327)
```text
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

**File:** VLMGP.sol (L252-268)
```text
    // @notice lock MGP in the contract
    // @param _amount the amount of MGP to lock
    function lock(uint256 _amount) override external whenNotPaused nonReentrant {
        _lock(msg.sender, msg.sender, _amount);

        emit NewLock(msg.sender, block.timestamp, _amount);
    }

    // @notice lock MGP in the contract
    // @param _amount the amount of MGP to lock
    // @param _for the address to lcock for
    // @dev the tokens will be taken from msg.sender
    function lockFor(uint256 _amount, address _for) override external whenNotPaused nonReentrant {
        _lock(msg.sender, _for, _amount);

        emit NewLock(_for, block.timestamp, _amount);
    }
```

**File:** VLMGP.sol (L313-367)
```text
    // @notice unlock a finished slot
    // @param slotIndex the index of the slot to unlock
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();

        address[] memory lps = new address[](1);
        address[][] memory vlMGPrewards = new address[][](1);
        lps[0] = address(this);
        IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender);

        uint256 unlockedAmount = slot.amountInCoolDown;
        _unlock(unlockedAmount);

        slot.amountInCoolDown = 0;
        IERC20(MGP).safeTransfer(msg.sender, unlockedAmount);

        emit Unlock(msg.sender, block.timestamp, unlockedAmount);
    }

    function cancelUnlock(uint256 _slotIndex) external override whenNotPaused {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        _checkInCoolDown(msg.sender, _slotIndex);

        totalAmountInCoolDown -= slot.amountInCoolDown; // reduce amount to cool down accordingly
        slot.amountInCoolDown = 0; // not in cool down anymore

        emit ReLock(msg.sender, _slotIndex, slot.amountInCoolDown);
    }

    // penalty caculation
    function forceUnLock(uint256 _slotIndex) external whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];
        _checkInCoolDown(msg.sender, _slotIndex);

        _unlock(slot.amountInCoolDown);
        (uint256 penaltyAmount, uint256 amountToUser) = expectedPenaltyAmount(_slotIndex);

        IERC20(MGP).safeTransfer(msg.sender, amountToUser);
        totalPenalty += penaltyAmount;

        slot.amountInCoolDown = 0;
        slot.endTime = block.timestamp;

        emit ForceUnLock(msg.sender, _slotIndex, amountToUser, penaltyAmount);
    }
```
