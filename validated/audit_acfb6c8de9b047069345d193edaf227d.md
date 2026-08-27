### Title
Reward-sniping via instant, non-streamed `queueNewRewards` lets an attacker flash-stake right before reward injection and capture yield with zero holding duration - (File: rewards/BaseRewardPool.sol / rewards/MasterMagpie.sol)

### Summary
`BaseRewardPool._provisionReward` (called from `queueNewRewards`) applies the *entire* injected reward amount to `rewardPerTokenStored` in a single instant, using the live `totalStaked()` at that exact block, instead of streaming it over time. Because `BaseRewardPool.balanceOf()` reads MasterMagpie's live `stakingInfo` and `earned()`/`_updateFor()` use that live balance against the checkpointed `rewardPerTokenStored`, an attacker who deposits a large stake immediately before the reward manager's `queueNewRewards` call and withdraws immediately after captures a proportional share of the whole reward despite a near-zero staking duration, diluting/stealing yield from genuine long-term stakers.

### Finding Description
`MasterMagpie._deposit` and `_harvestAndUnstake` call `_harvestBaseRewarder`, which invokes `rewarder.updateFor(_account)` [1](#0-0) . `updateFor` -> `_updateFor` sets `userRewards[token][_account] = earned(_account, token)` and checkpoints `userRewardPerTokenPaid[token][_account] = rewardPerToken(token)` [2](#0-1) . `earned()` computes `balanceOf(_account) * (rewardPerToken - paid)`, where `balanceOf` reads **live** from `IMasterMagpie(operator).stakingInfo(stakingToken, _account)` [3](#0-2) .

Critically, `queueNewRewards` -> `_provisionReward` distributes the *entire* new reward instantly:
```
rewardInfo.rewardPerTokenStored += (_amountReward * 10**stakingDecimals()) / this.totalStaked();
``` [4](#0-3) 
There is no time-based streaming (no `rewardRate`/`periodFinish`); the whole reward is baked into `rewardPerTokenStored` using whatever `totalStaked()` is at that exact block.

Exploit flow:
1. Attacker front-runs the reward manager's `queueNewRewards(...)` tx by calling `MasterMagpie.deposit`/`depositFor` with a large `_amount` of the pool's staking token in the same block, just before it. `_deposit` calls `_harvestBaseRewarder` (checkpointing with the *old*, pre-deposit balance — correct) and then increases `user.amount` to the large value [5](#0-4) .
2. Reward manager's `queueNewRewards` executes; `totalStaked()` now includes the attacker's freshly deposited large stake, so the reward-per-token increment is diluted for everyone, but the attacker is now entitled to `attackerBalance / totalStaked` of the whole injected reward.
3. Attacker immediately calls `MasterMagpie.withdraw`/`withdrawFor`, which triggers `_harvestAndUnstake` -> `_harvestBaseRewarder` -> `updateFor(attacker)`, checkpointing `earned = largeBalance * (newRewardPerToken - depositTimeRewardPerToken)` into `userRewards` [6](#0-5) .
4. Attacker claims via `getReward`/`_claimBaseRewarder` to receive the actual tokens and withdraws principal, having held the stake for effectively zero duration relative to the reward's intended accrual/emission period.

No existing check prevents this: there is no minimum staking duration, no time-weighted average balance, and no streaming rate limiting how much of a reward injection can be captured within a single block. `nonReentrant`/`whenNotPaused` guard against reentrancy/pausing, not against this timing/accounting issue.

### Impact Explanation
This is theft of unclaimed yield from legitimate, longer-term stakers in a given `BaseRewardPool`. Every time a reward manager calls `queueNewRewards`, an attacker who front-runs with a large deposit and back-runs with a withdrawal can claim a share of that specific reward injection proportional to their instantaneous stake, with the share coming at the expense of other stakers who were staked over the actual emission period. This matches the "theft of unclaimed yield" Immunefi impact class.

### Likelihood Explanation
This requires no special privileges — only capital to acquire/mint the pool's staking token and the ability to front-run/back-run a manager's `queueNewRewards` transaction (mempool visibility, higher gas, or bundling), which is standard MEV capability available to any unprivileged actor. It is repeatable on every reward injection to any `BaseRewardPool`/`BaseRewardPoolV2` pool. The magnitude of theft scales with how large a stake the attacker can bring in for that single block relative to existing `totalStaked()`.

### Recommendation
Stream new rewards over time instead of crediting them instantly (introduce `rewardRate`/`periodFinish` similar to Synthetix `StakingRewards`, updating `rewardPerTokenStored` based on elapsed time rather than a lump sum), and/or require a minimum staking duration before a deposit contributes to `totalStaked()` for reward-accrual purposes (e.g., checkpoint balances with a time-weighted mechanism instead of using the instantaneous `balanceOf`).

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `MasterMagpie`, a `BaseRewardPool` pool with a reward token, and two staker accounts: `victim` (staked early, long before reward) and `attacker` (no prior stake).
2. Advance time so `victim` has been staked for a long period with no rewards yet queued.
3. In a single block: 
   a. `attacker` calls `MasterMagpie.deposit(stakingToken, largeAmount)`.
   b. Reward manager calls `rewarder.queueNewRewards(rewardAmount, rewardToken)`.
   c. `attacker` calls `MasterMagpie.withdraw(stakingToken, largeAmount)`.
4. Assert `rewarder.earned(attacker, rewardToken) > 0` despite `attacker`'s staking duration being 0 blocks/seconds.
5. Assert `attacker`'s captured share (`largeAmount / totalStakedAtInjection * rewardAmount`) reduces the effective reward now available/attributable to `victim` (compare `victim`'s earned reward with and without the attacker's flash stake in two otherwise identical scenarios), confirming zero-duration stake diverts yield from the honest staker.

### Citations

**File:** rewards/MasterMagpie.sol (L482-498)
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
```

**File:** rewards/MasterMagpie.sol (L516-534)
```text
    function _harvestAndUnstake(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        updatePool(_stakingToken);

        UserInfo storage user = userInfo[_stakingToken][_account];

        if (!_isVlMgp && user.available < _amount)
            revert WithdrawAmountExceedsStaked();
        else if(user.amount < _amount && _isVlMgp)
            revert UnlockAmountExceedsLocked();
        
        _harvestMGP(_stakingToken, _account);
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount - _amount;
        
        if(!_isVlMgp)
            user.available = user.available - _amount;
        user.rewardDebt = (user.amount * tokenToPoolInfo[_stakingToken].accMGPPerShare) / 1e12;
    }
```

**File:** rewards/MasterMagpie.sol (L631-636)
```text
    /// only update the reward counting on in base rewarder but not sending them to user
    function _harvestBaseRewarder(address _stakingToken, address _account) internal {
        IBaseRewardPool rewarder = IBaseRewardPool(tokenToPoolInfo[_stakingToken].rewarder);
        if (address(rewarder) != address(0))
            rewarder.updateFor(_account);
    }
```

**File:** rewards/BaseRewardPool.sol (L130-185)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }

    /// @notice Returns amount of reward token per staking tokens in pool
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token per staking tokens in pool
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }

    function rewardTokenInfos()
        override
        external
        view
        returns
        (
            address[] memory bonusTokenAddresses,
            string[] memory bonusTokenSymbols
        )
    {
        uint256 rewardTokensLength = rewardTokens.length;
        bonusTokenAddresses = new address[](rewardTokensLength);
        bonusTokenSymbols = new string[](rewardTokensLength);
        for (uint256 i; i < rewardTokensLength; i++) {
            bonusTokenAddresses[i] = rewardTokens[i];
            bonusTokenSymbols[i] = IERC20Metadata(address(bonusTokenAddresses[i])).symbol();
        }
    }

    /// @notice Returns amount of reward token earned by a user
    /// @param _account Address account
    /// @param _rewardToken Address reward token
    /// @return Returns amount of reward token earned by a user
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return (
            (((balanceOf(_account) *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                (10**stakingDecimals())) + userRewards[_rewardToken][_account])
        );
    }
```

**File:** rewards/BaseRewardPool.sol (L288-295)
```text
    function _updateFor(address _account) internal {
        uint256 length = rewardTokens.length;
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = rewardTokens[index];
            userRewards[rewardToken][_account] = earned(_account, rewardToken);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
    }
```

**File:** rewards/BaseRewardPool.sol (L297-318)
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
        if (this.totalStaked() == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10**stakingDecimals()) /
                this.totalStaked();
        }
```
