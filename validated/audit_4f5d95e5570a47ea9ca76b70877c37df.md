### Title
Reward Injection Can Be Sandwiched via Deposit/Withdraw to Steal Rewards From Other Stakers - ([File: rewards/BaseRewardPool.sol])

### Summary
`BaseRewardPool` distributes rewards to all currently staked accounts proportionally to their share of `totalStaked()` at the exact moment a reward is provisioned, with no time-weighting or vesting. `donateRewards()` is a permissionless external function that immediately triggers `_provisionReward()`, and `queueNewRewards()` (called periodically by a manager/keeper) does the same. An unprivileged wallet can front-run a reward injection by depositing into the pool via `MasterMagpie`, capture a proportional share of the newly injected reward instantly, then withdraw right after — exactly analogous to the reported L2 oracle sandwich, where a manually/periodically updated value creates a window that can be front-run and back-run for profit at the expense of legitimate long-term stakers.

### Finding Description
`_provisionReward()` updates `rewardPerTokenStored` using the pool's `totalStaked()` at call time: [1](#0-0) 

This is invoked both by the permissioned `queueNewRewards()` (manager-only, e.g. periodic harvest/keeper calls) and by the fully permissionless `donateRewards()`: [2](#0-1) 

A user's earned reward is computed purely from the delta between the current `rewardPerTokenStored` and the value recorded the last time their balance changed (`userRewardPerTokenPaid`), with no minimum staking duration: [3](#0-2) [4](#0-3) 

Because `balanceOf()` reads live stake from `MasterMagpie`'s `stakingInfo`, any wallet that deposits stake into the tracked `stakingToken` immediately before a reward provisioning event (whether it's a manager's periodic `queueNewRewards()` call or someone else's `donateRewards()` call) begins accruing a full pro-rata share of that reward the instant it lands, and can withdraw right after: [5](#0-4) 

This mirrors the reported bug class precisely: a periodically/externally updated accounting value (there: oracle rate; here: `rewardPerTokenStored`) creates a discrete jump that can be front-run and back-run by an ordinary wallet using only deposit/withdraw, diluting the return earned by genuine long-term stakers who had capital at risk for the full period the reward accrued.

### Impact Explanation
Every time a reward is injected (whether by a keeper's periodic `queueNewRewards()` or by anyone calling `donateRewards()`), a sandwiching wallet can extract a disproportionate share of that reward without having contributed to the pool beforehand, directly stealing yield that would otherwise accrue to legitimate long-term stakers. This is a direct theft of unclaimed/pending yield from other users, reachable by any unprivileged wallet with only two transactions (deposit, then withdraw) around a public event.

### Likelihood Explanation
`donateRewards()` has no access control at all, so an attacker can even trigger the reward injection themselves in the same block as their deposit/withdraw sandwich, making exploitation fully self-contained and requiring no reliance on external actors. Even for `queueNewRewards()` (manager-only), harvest/reward-queue calls are typically periodic and visible in the mempool, making them easy to front-run. The lack of any deposit lock-up, vesting, or minimum holding period in the reward-per-token math makes this trivially and repeatedly exploitable.

### Recommendation
Introduce time-weighting for reward distribution (e.g., streaming rewards over a duration via `rewardRate`/`periodFinish` similar to Synthetix-style `StakingRewards`, rather than an instantaneous lump-sum `rewardPerTokenStored` bump), and/or require a minimum staking duration before a deposit is eligible to earn from a given reward injection.

### Proof of Concept
1. Attacker observes pending reward tokens in the manager's wallet destined for `queueNewRewards()` (or attacker holds their own reward tokens to call `donateRewards()` directly).
2. Attacker deposits a large amount of `stakingToken` into `MasterMagpie` for the target pool, front-running the reward call.
3. Reward is provisioned via `queueNewRewards()`/`donateRewards()`, calling `_provisionReward()` at `rewards/BaseRewardPool.sol:297-319`, which raises `rewardPerTokenStored` proportional to `totalStaked()` including the attacker's just-added stake.
4. Attacker immediately calls `getReward()` (`rewards/BaseRewardPool.sol:221-240`) to claim their pro-rata share, then withdraws their stake from `MasterMagpie` — having earned a full reward share despite holding stake for only one block, diluting rewards owed to genuine long-term stakers.

### Citations

**File:** rewards/BaseRewardPool.sol (L130-136)
```text
    /// @notice Returns amount of staked tokens in master magpie by account
    /// @param _account Address account
    /// @return Returns amount of staked tokens by account
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPool.sol (L173-185)
```text
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

**File:** rewards/BaseRewardPool.sol (L258-284)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only callable by manager
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
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

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }

    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

**File:** rewards/BaseRewardPool.sol (L297-319)
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
        emit RewardAdded(_amountReward, _rewardToken);
```
