### Title
Reward Sniping via Flash Deposit/Withdraw Around Reward Provisioning — Interest/Time-Free Yield Capture - (File: rewards/BaseRewardPool.sol, rewards/BaseRewardPoolV2.sol, rewards/MasterMagpie.sol)

### Summary
`BaseRewardPool`/`BaseRewardPoolV2` distribute rewards by instantly bumping `rewardPerTokenStored` based on the pool's `totalStaked()` snapshot at the exact moment `queueNewRewards`/`donateRewards` executes, with no time-weighted accrual and no minimum staking duration enforced by `MasterMagpie`'s `deposit`/`withdraw`. This mirrors the referenced bug class (interest/state updated only at discrete points, with no cost for entering/exiting around that update), letting a user capture a proportional share of freshly injected rewards without having contributed liquidity/stake during the period those rewards accrued, by depositing immediately before and withdrawing immediately after the reward injection in the same block.

### Finding Description
`_provisionReward` in `BaseRewardPool.sol` and `BaseRewardPoolV2.sol` updates `rewardInfo.rewardPerTokenStored` in a single, instantaneous step using `totalStaked()` at call time: [1](#0-0) 

`rewardPerToken()` simply returns this stored value with no time component: [2](#0-1) 

A user's claimable amount is computed purely from the difference between the current `rewardPerTokenStored` and the value recorded at `_updateFor`, multiplied by their current stake — there is no requirement that the stake existed while rewards were accruing: [3](#0-2) [4](#0-3) 

`MasterMagpie._deposit`/`_withdraw`/`_harvestAndUnstake` (which drive stake accounting used by `balanceOf()` in the reward pools) impose no cooldown, minimum holding period, or same-block restriction: [5](#0-4) 

Because `queueNewRewards` (called during periodic harvest/compounding flows, e.g. from `WombatStaking.sol`) and `donateRewards` (permissionless, gated only by `isRewardToken`) both funnel into `_provisionReward`, an attacker can:
1. Front-run a pending reward-provisioning transaction with `deposit()`/`depositFor()` into the target staking token pool via `MasterMagpie`.
2. Let the reward provisioning transaction execute, instantly inflating `rewardPerTokenStored` proportional to the attacker's now-included stake.
3. Back-run with `withdraw()` in the same block, immediately harvesting/claiming their share of `earned()` and removing their capital.

This is directly analogous to the referenced report's interest-free-loan pattern: state (interest rate / reward-per-share) is updated at a discrete checkpoint that a market participant can straddle costlessly within one block, extracting economic benefit (yield) without bearing the corresponding cost (time-at-risk / capital lockup that legitimate long-term stakers bear).

### Impact Explanation
Existing long-term stakers are permanently diluted: reward tokens that should have accrued to them based on their sustained stake are instead partially redirected to the transient flash-staker, resulting in permanent theft of unclaimed yield from genuine depositors — matching the "theft or permanent freezing of unclaimed yield" impact bar. This applies broadly across every pool using `BaseRewardPool`/`BaseRewardPoolV2` (MGP staking pools, WombatStaking-derived receipt token pools, etc.) since none of them enforce time-weighted reward accrual.

### Likelihood Explanation
The attack requires only ordinary, unprivileged wallet calls (`deposit`/`withdraw` on `MasterMagpie`) and mempool visibility of a `queueNewRewards` call (routine harvest/compounding transactions, which are frequent and often predictable/schedulable). No governance, oracle, or admin privilege is needed, and the cost is limited to gas plus temporary capital exposure for a single block — comparable in profile to the referenced report's low-cost MEV-style exploitation.

### Recommendation
Introduce time-weighted reward accrual (e.g., checkpoint-based `rewardPerSecond` distribution as used in `MultiRewarderPerSec.sol`/`MasterMagpie.updatePool` rather than instantaneous lump-sum injection), or require a minimum staking duration / block-delay between deposit and reward eligibility (e.g., snapshotting stake balances before allowing `withdraw` in the same block as a `queueNewRewards` call) so rewards accrue proportionally to time staked rather than to a stake snapshot at injection time.

### Proof of Concept
1. Attacker monitors mempool for a `queueNewRewards(_amountReward, _rewardToken)` call on a `BaseRewardPool` (typically triggered by protocol harvest flows).
2. Attacker front-runs with `MasterMagpie.deposit(_stakingToken, largeAmount)`.
3. `queueNewRewards` executes, running `_provisionReward` at [1](#0-0) , which increases `rewardPerTokenStored` using `totalStaked()` that now includes the attacker's freshly added stake.
4. Attacker back-runs with `MasterMagpie.withdraw(_stakingToken, largeAmount)`, triggering `_harvestAndUnstake` → harvest of the pool's `earned()` value computed via `rewardPerToken()` at [2](#0-1) , capturing a share of the reward proportional to stake with zero time-at-risk, all within one block.

### Citations

**File:** rewards/BaseRewardPool.sol (L141-148)
```text
    function rewardPerToken(address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return rewards[_rewardToken].rewardPerTokenStored;
    }
```

**File:** rewards/BaseRewardPool.sol (L286-295)
```text
    /* ============ Internal Functions ============ */

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

**File:** rewards/BaseRewardPoolV2.sol (L316-321)
```text
    function _earned(address _account, address _rewardToken, uint256 _userShare) internal view returns (uint256) {
        return ((_userShare *
                (rewardPerToken(_rewardToken) -
                    userRewardPerTokenPaid[_rewardToken][_account])) /
                10**stakingTokenDecimals) + userRewards[_rewardToken][_account];
    }
```

**File:** rewards/MasterMagpie.sol (L481-514)
```text
    /// @notice internal function to deal with deposit staking token
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

    /// @notice internal function to deal with withdraw staking token
    function _withdraw(address _stakingToken, address _account, uint256 _amount, bool _isVlMgp) internal {
        _harvestAndUnstake(_stakingToken, _account, _amount, _isVlMgp);

        if (!_isVlMgp)
            IERC20(tokenToPoolInfo[_stakingToken].stakingToken).safeTransfer(address(msg.sender), _amount);
        emit Withdraw(_account, _stakingToken, _amount);
    }
```
