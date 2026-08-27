### Title
Self-recycling of forfeited vlMGP rewards defeats the early-unlock penalty when the forfeiter dominates `totalStaked()` - ([File: rewards/vlMGPBaseRewarder.sol])

### Summary
`vlMGPBaseRewarder._calExpireForfeit` computes a forfeit amount based on `VLMGP.getRewardablePercentWAD`, which drops below 100% once a user calls `startUnlock`. The forfeited amount is immediately re-injected into the reward pool via `_queueNewRewardsWithoutTransfer`, distributed pro-rata over `totalStaked()`. Because `totalStaked()` for the vlMGP pool is the global `vlMGP.totalSupply()` and a user's `balanceOf` (locked + cooling-down) is unaffected by `startUnlock`, a sole/majority staker recaptures nearly all of their own "forfeited" reward on the next `getReward` call, nullifying the penalty meant to benefit other lockers.

### Finding Description
- `VLMGP.balanceOf(_user) = getUserTotalLocked(_user) + getUserAmountInCoolDown(_user)` [1](#0-0)  — starting an unlock only moves an amount from "locked" to "cooldown"; it does not reduce the user's total balance, and it does not withdraw from `masterMagpie`, so `totalStaked()` in the rewarder (`IERC20(vlMGP).totalSupply()`) stays constant [2](#0-1) .
- `VLMGP.startUnlock` only claims currently accrued rewards (at the pre-unlock 100% rate) and then registers the cooldown slot, reducing `getRewardablePercentWAD` for any reward accrued afterward [3](#0-2) [4](#0-3) .
- On a subsequent `getReward`, `_sendReward` computes `forfeitAmount = _calExpireForfeit(...)` using the now-reduced `getRewardablePercentWAD`, sends `toSend` to the user, and re-queues `forfeitAmount` via `_queueNewRewardsWithoutTransfer` [5](#0-4) .
- `_queueNewRewardsWithoutTransfer` increases `rewardPerTokenStored` by `forfeitAmount * 10**decimals / totalStaked()` [6](#0-5) . Since the forfeiter's own `balanceOf` is still counted inside `totalStaked()` (indeed it dominates or equals it when they are the sole/majority staker), the very next reward accrual attributes most/all of that re-queued amount back to the same forfeiter.
- Repeating `getReward` lets the attacker capture essentially the entire forfeited amount over successive claims (each round only losing the same shrinking percentage again), because the "penalty pool" never actually leaves their own economic share of the vault. This breaks the intended conservation property that forfeited yield should benefit other, honest long-term lockers — it instead flows straight back to the forfeiter when they hold most of the stake.
- No modifier, `nonReentrant` guard, or accounting check prevents this: `nonReentrant` only blocks re-entrancy, not sequential calls, and `updateRewards`/`onlyMasterMagpie` do not validate the source of `totalStaked()` weighting.

### Impact Explanation
This is a theft/misallocation of unclaimed yield: reward tokens meant to be forfeited to other vlMGP lockers as an early-unlock penalty are instead reclaimed by the forfeiter themselves whenever they represent most of `totalStaked()`. In a pool where one or few large holders dominate, this materially defeats the anti-early-unlock incentive design and can drain value intended for minority long-term lockers proportionally to the dominant holder's share. This matches the "theft of unclaimed yield meant for other lockers" impact class.

### Likelihood Explanation
Feasible and fully permissionless: any user (or set of colluding users) who dominates the vlMGP pool's `totalStaked()` can trigger this with ordinary calls — `lock`, `startUnlock`, `getReward` (multiple times). No special privileges, flash loans, or admin rights required. The effect scales with the attacker's share of `totalStaked()`; it is most severe (near 100% recapture) for a sole staker, but even a majority (not sole) holder recovers a disproportionate share of forfeited rewards relative to minority lockers, which is repeatable indefinitely as new rewards are queued.

### Recommendation
Exclude the forfeiting account's own stake from the redistribution basis when re-queuing forfeited rewards (e.g., distribute forfeited amounts over `totalStaked() - balanceOf(_account)`, or track/checkpoint per-user "penalized" shares separately so the forfeiter cannot re-earn from their own penalty), or hold forfeited funds in an unclaimable pool for a cool-down period during which the forfeiting account's share is not eligible for the redistributed slice.

### Proof of Concept
Hardhat test outline:
1. Deploy `VLMGP`, `vlMGPBaseRewarder`, `masterMagpie` mocks/production wiring per existing test harness.
2. Locker A (attacker) locks `A` MGP, becoming the sole (or >90%) staker of the vlMGP pool.
3. Queue a reward `R1` via `queueNewRewards` (rewardManager pushes protocol rewards) — verify `rewardPerTokenStored` increases based on `totalStaked() == A`.
4. Attacker calls `startUnlock(partial X)` — verify it first claims pending reward at 100% rewardable percent (baseline, no forfeiture yet).
5. Queue another reward `R2`. Attacker calls `getReward` — assert `_calExpireForfeit` returns nonzero `forfeitAmount = R2 * (1 - rewardablePercentWAD)/1e18`, and `toSend = R2 - forfeitAmount` was transferred.
6. Assert `_queueNewRewardsWithoutTransfer` bumped `rewardPerTokenStored` by `forfeitAmount / totalStaked()`.
7. Attacker calls `getReward` again (no new external reward queued) — assert attacker receives a further payout ≈ `forfeitAmount * rewardablePercentWAD/1e18`, i.e., recovers most of their own forfeited amount.
8. Repeat step 7 in a loop and assert cumulative recovered amount approaches `forfeitAmount` (bounded only by the 0.1% dust-ignore threshold), demonstrating recovered share ≫ the attacker's "true" entitled share if other lockers existed to absorb the penalty.
9. Contrast with a second scenario where Locker B holds an equal stake and never unlocks — show B's share of the forfeited amount is diluted by A's own reclaim, proving the penalty fails to fully benefit B as designed.

### Citations

**File:** VLMGP.sol (L113-115)
```text
    function balanceOf(address _user) public override view returns (uint256) {
        return getUserTotalLocked(_user) + getUserAmountInCoolDown(_user);
    }
```

**File:** VLMGP.sol (L193-218)
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
                }

            }
        }

        return percent;
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

**File:** rewards/vlMGPBaseRewarder.sol (L134-139)
```text
    /// @notice Returns total current lock weighting, lock weighting is calculated by 
    /// amount of MGP still in lock + amount of MGP in cool down / 2
    /// @return Returns current amount of staked tokens
    function totalStaked() public override view returns (uint256) {
        return IERC20(address(vlMGP)).totalSupply();
    }
```

**File:** rewards/vlMGPBaseRewarder.sol (L331-346)
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
```

**File:** rewards/vlMGPBaseRewarder.sol (L363-376)
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
```
