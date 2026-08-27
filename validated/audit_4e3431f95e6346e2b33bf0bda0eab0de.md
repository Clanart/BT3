### Title
Front-running JIT stake into VLMGP/mWomSV to steal forfeiture-redistributed rewards - (File: `wombat/SmartWomConvert.sol`, `rewards/vlMGPBaseRewarder.sol`, `rewards/mWOMSVBaseRewarder.sol`)

### Summary
`vlMGPBaseRewarder` and `mWOMSVBaseRewarder` distribute forfeited MGP/mWOM (from `forceUnLock`/`queueMGP`) via a lump-sum `rewardPerTokenStored` bump proportional to `totalStaked()` at the moment of forfeiture, with no time-weighting or vesting. `SmartWomConvert.convertFor(..., 2)` lets any unprivileged caller instantly mint mWomSV via `mWomSV.lockFor`, so an attacker can front-run a pending large forfeiture (e.g. `forceUnLock`/`queueMGP`), increase `totalStaked()` just before the forfeit tx executes, and immediately claim a pro-rata share of the forfeited rewards with zero time-weighted contribution.

### Finding Description
`SmartWomConvert._convertFor` with `_mode == 2` calls `mWomSV.lockFor(obtainedmWomAmount, _for)` [1](#0-0) , which via `_lock` increases the caller's stake registered in `MasterMagpie`, and thus increases `balanceOf`/`totalStaked()` as seen by `mWOMSVBaseRewarder` and (analogously) `VLMGP.lockFor` for `vlMGPBaseRewarder`.

Reward distribution in both rewarders is not streamed per-second; it is a single-shot injection triggered at the moment a forfeiture occurs:
- `mWOMSVBaseRewarder._sendReward` → `_queueNewRewardsWithoutTransfer` bumps `rewardInfo.rewardPerTokenStored += forfeitAmount * 10**decimals / totalStaked()` [2](#0-1) .
- `vlMGPBaseRewarder.queueMGP`/`_sendReward` does the same for vlMGP forfeitures from `forceUnLock`/`queueMGP` [3](#0-2) [4](#0-3) .

Because `rewardPerTokenStored` is only updated at discrete forfeiture events (not continuously), a staker's index is checkpointed at deposit time via `updateRewards`/`updateReward` modifiers (`userRewardPerTokenPaid[token][account] = rewardPerToken(token)` at the *current* value) [5](#0-4) . If the attacker's `lockFor` transaction lands in an earlier position (front-run) than the forfeiture transaction within the mempool ordering, the attacker's checkpoint is set to the pre-forfeit `rewardPerTokenStored`, and their newly-added balance is included in `totalStaked()` when the forfeit-triggered `_queueNewRewardsWithoutTransfer` executes. The attacker's subsequent `_earned`/`getReward` call then yields `balance * (rewardPerTokenStored_after - checkpoint_before)`, i.e. a full pro-rata share of the entire forfeited amount, despite holding the stake for effectively zero time.

Reward claiming (`getReward`, routed through `MasterMagpie.multiclaimFor`) is decoupled from principal withdrawal: claiming rewards does not require going through `startUnlock`'s cooldown period, so the attacker can extract the ill-gotten reward immediately, independent of whether/when they eventually unlock their principal. No mechanism (minimum holding period, streaming/vesting of rewards, or reward-per-block linearization) prevents this. `nonReentrant`/`whenNotPaused` modifiers do not address this because the issue is about state-at-a-point-in-time accounting, not reentrancy.

### Impact Explanation
This is theft of unclaimed yield: an attacker with capital sufficient to briefly out-stake existing long-term lockers can redirect a share of forfeiture-redistributed rewards (which are intended to compensate long-term lockers who did not forfeit) to themselves, for zero real economic contribution/time. This matches "theft of unclaimed yield" under the class taxonomy. The magnitude scales with the attacker's temporary stake size relative to `totalStaked()` and the size of the pending forfeiture event.

### Likelihood Explanation
Feasibility is high for anyone monitoring the mempool: forfeiture transactions (`forceUnLock`, `queueMGP`) are public before confirmation, and `SmartWomConvert.convertFor(...,2)` / `VLMGP.lockFor` are unprivileged, permissionless, front-runnable calls requiring only capital (WOM/MGP tokens) and gas to place a higher-priority (or same-block, earlier-index) transaction. The attack is repeatable for every sizeable forfeiture event and does not require flash loans if the attacker holds/borrows the base asset, though a flash loan of WOM/MGP could also be used to size up the temporary stake (subject to the underlying lock/cooldown token not being flash-loanable back out same block, since MGP/mWom must be locked, not merely held — this bounds attacker capital efficiency but does not prevent the exploit if attacker has capital available for the block).

### Recommendation
Redesign the forfeiture-redistribution mechanism to avoid single-block, non-time-weighted reward-per-token bumps: e.g., stream forfeited rewards over a vesting/decay window (similar to Synthetix-style `rewardRate`/`periodFinish` streaming) rather than instantaneous `rewardPerTokenStored` increments, or require a minimum staking duration before newly staked balance becomes reward-eligible for lump-sum distributions, or snapshot `totalStaked()`/eligible balances prior to same-block deposits (e.g., via checkpoints keyed to block number) so JIT deposits cannot capture the same-block forfeiture. At minimum, require a lock-up delay before new stake counts toward `totalStaked()` for a forfeit distribution.

### Proof of Concept
Foundry test outline:
1. Deploy `VLMGP`/`vlMGPBaseRewarder` (or `mWomSV`/`mWOMSVBaseRewarder`) with existing long-term staker `Alice` holding a lock for N days, with `rewardPerTokenStored` at some baseline.
2. Have `Bob` (a victim) prepare a `forceUnLock` call on a large locked slot, which will trigger `_calExpireForfeit` producing a large `forfeitAmount` and calling `_queueNewRewardsWithoutTransfer`.
3. Simulate front-run ordering: in the same block, before Bob's `forceUnLock` tx executes, have `Attacker` call `SmartWomConvert.convertFor(_amountIn, DENOMINATOR, 0, attacker, 2)` (or `VLMGP.lockFor`) to add a large stake.
4. Execute Bob's `forceUnLock`.
5. Assert: `vlMGPBaseRewarder.earned(attacker, MGP) > 0` (or `mWOMSVBaseRewarder.earned`), proportional to `attackerStake / totalStaked()` at forfeiture time.
6. Call `getReward`/`multiclaimFor` for attacker and assert MGP/mWOM is actually transferred to attacker in the same block, with zero elapsed time since `lockFor`.
7. Compare to a control scenario where `Alice` (long-term staker) receives a diminished share due to the attacker's dilution, demonstrating value transfer from long-term lockers to the JIT staker.

### Citations

**File:** wombat/SmartWomConvert.sol (L212-214)
```text
        } else if (_mode == 2) {
            IERC20(mWom).safeApprove(address(mWomSV), obtainedmWomAmount);
            mWomSV.lockFor(obtainedmWomAmount, _for);
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

**File:** rewards/vlMGPBaseRewarder.sol (L100-113)
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
