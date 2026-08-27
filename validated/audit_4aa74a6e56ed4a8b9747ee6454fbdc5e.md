This confirms the mechanism: `_provisionReward` applies the entire reward amount to `rewardPerTokenStored` **instantaneously** at the moment `queueNewRewards` is called, with no vesting period, `periodFinish`, or `rewardRate`-style linear release over time — unlike a standard Synthetix `StakingRewards` design.### Title
Instant, non-vested reward-index update in `BaseRewardPoolV2` allows front-running `queueNewRewards` to steal yield from long-term stakers - ([File: rewards/BaseRewardPoolV2.sol])

### Summary
`BaseRewardPoolV2._provisionReward` (invoked by `queueNewRewards`) applies the entire newly-added reward amount to `rewardPerTokenStored` in a single instantaneous step, with no time-weighting, vesting period, or `rewardRate`/`periodFinish` mechanism. Because `earned()`/`_earned()` compute a user's share purely from their **current** staked balance versus the stored per-token index delta, an attacker can deposit a large stake into `MasterMagpie` immediately before a predictable/publicly-visible `queueNewRewards` transaction, claim a disproportionate share of the reward, and withdraw immediately after, diluting and stealing yield that should have accrued to genuine long-term stakers.

### Finding Description
- `MasterMagpie.deposit` → `_deposit` (`rewards/MasterMagpie.sol:482-505`) calls `_harvestBaseRewarder` (which calls `rewarder.updateFor(_account)`, checkpointing `userRewardPerTokenPaid` at the **pre-deposit** `rewardPerTokenStored`) and only afterwards increases `user.amount`/`user.available`. This means a brand-new depositor's `userRewardPerTokenPaid` baseline is set right before their balance jumps, and they will earn on any subsequent index increase in full proportion to their new (large) balance. [1](#0-0) 
- `BaseRewardPoolV2.totalStaked()` reads live `IERC20(stakingToken).balanceOf(operator)` and `balanceOf(_account)` reads the live `stakingInfo` from `MasterMagpie` — both reflect the attacker's deposit the instant it lands, with no lock or delay. [2](#0-1) 
- `queueNewRewards` → `_provisionReward` immediately folds the entire `_amountReward` into `rewardInfo.rewardPerTokenStored` proportionally to `totalStaked()` at that exact block — there is no `rewardRate`/`periodFinish` linear-release logic anywhere in this contract (confirmed absent via search across the rewarder contracts). [3](#0-2) 
- `earned()`/`_earned()` computes reward strictly as `balanceOf(_account) * (rewardPerToken - userRewardPerTokenPaid) / decimals`, i.e., purely a snapshot-based split with zero regard for how long the balance was actually held. [4](#0-3) 
- After `queueNewRewards` lands, the attacker calls `MasterMagpie._multiClaim`/`getReward` (via `_claimBaseRewarder` → `rewarder.getReward`) to harvest their inflated share, then calls `withdraw`/`_withdraw` to reclaim the huge principal — both unguarded by any minimum holding period. [5](#0-4) [6](#0-5) 

None of `nonReentrant`, `whenNotPaused`, or the reward-index update logic prevent this, because they only guard against same-transaction reentrancy/pausing — they do not enforce any minimum staking duration or vesting for reward eligibility. `onlyManager` protects who can call `queueNewRewards`, but does nothing to stop an unprivileged party from depositing right before/withdrawing right after that call is observed in the mempool.

### Impact Explanation
This is a theft of unclaimed yield from legitimate long-term stakers: the attacker's flash/short-term deposit dilutes `totalStaked()` at the exact moment `rewardPerTokenStored` is bumped, then extracts a proportional (and often majority, if capital is large enough relative to existing TVL) slice of the newly-queued rewards despite having zero economic exposure to the pool over time. This matches the Immunefi impact class "theft of unclaimed yield."

### Likelihood Explanation
- Requires only capital access (flash loan or DEX swap) to acquire the staking token temporarily — no privileged role needed.
- Requires `queueNewRewards` calls to be observable/predictable in the mempool (e.g., periodic keeper-triggered harvest calls), which is realistic for most reward distribution flows in this protocol (harvest bots, scheduled compounding).
- Repeatable every time a `queueNewRewards` transaction is broadcast, and profitability scales with the attacker's available capital relative to existing TVL.

### Recommendation
Introduce time-weighted/vested reward distribution (e.g., Synthetix-style `rewardRate` + `periodFinish` linear release over a fixed duration) instead of instantaneously folding the full reward amount into `rewardPerTokenStored`. Alternatively, enforce a minimum staking duration before a deposit is eligible to earn from rewards queued after it (e.g., checkpoint eligibility only for balances that existed prior to the block in which `queueNewRewards` was called).

### Proof of Concept
Foundry fork test plan:
1. Deploy/fork `MasterMagpie` + `BaseRewardPoolV2` with an existing "long-term staker" holding `X` tokens staked for many blocks.
2. Simulate mempool observation of a manager's pending `queueNewRewards(rewardAmount, rewardToken)` transaction.
3. Attacker (unprivileged EOA), using flash-loaned/swapped staking tokens, calls `MasterMagpie.deposit(stakingToken, hugeAmount)` in the block immediately preceding the manager's `queueNewRewards` call (or same block, ordered before it).
4. Execute the manager's `queueNewRewards` transaction.
5. Attacker immediately calls `multiClaim`/`getReward` to harvest rewards, then `withdraw(stakingToken, hugeAmount)`.
6. Assert: `attacker_harvested_reward / 1_block_held` ratio vastly exceeds `long_term_staker_harvested_reward / blocks_held` ratio, and that the long-term staker's `earned()` value strictly decreased (in absolute reward-token terms attributable to that reward event) compared to a control run without the attacker's front-run deposit.

### Citations

**File:** rewards/MasterMagpie.sol (L482-505)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }
```

**File:** rewards/MasterMagpie.sol (L618-636)
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

    /// only update the reward counting on in base rewarder but not sending them to user
    function _harvestBaseRewarder(address _stakingToken, address _account) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0))
            rewarder.updateFor(_account);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L124-136)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
    }

    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L218-235)
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
            uint256 reward = userRewards[rewardToken][_account]; // updated during updateReward modifier
            if (reward > 0) {
                _sendReward(rewardToken, _account, _receiver, reward);
            }
        }

        return true;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L290-313)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20(_rewardToken).safeTransferFrom(
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
                (_amountReward * 10**stakingTokenDecimals) /
                totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
```

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```
