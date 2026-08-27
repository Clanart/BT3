### Title
Permissionless `donateRewards()` instantly updates `rewardPerTokenStored`, enabling risk-free JIT-stake sandwich theft of donated rewards - (File: rewards/BaseRewardPool.sol / rewards/BaseRewardPoolV2.sol / rewards/mWOMSVBaseRewarder.sol)

### Summary
`donateRewards()` in `BaseRewardPool`, `BaseRewardPoolV2`, and `mWOMSVBaseRewarder` is callable by **any unprivileged wallet** with no access control, and immediately folds the donated amount into `rewardPerTokenStored` based on the *current* `totalStaked()` snapshot. This mirrors the reported bug class (an instant, non-organic change to a value that determines pro-rata payout, without any time-weighting), which lets an attacker deposit right before the update and withdraw right after to capture a disproportionate share of the reward — exactly the "sandwich the instantaneous state change" pattern from the Hats report, except here the trigger is a normal user transaction, not an admin call.

### Finding Description
`_provisionReward()` computes:
```solidity
rewardInfo.rewardPerTokenStored =
    rewardInfo.rewardPerTokenStored +
    (_amountReward * 10**stakingDecimals()) / this.totalStaked();
``` [1](#0-0) 

and it is reachable from the fully permissionless entry point:
```solidity
function donateRewards(uint256 _amountReward, address _rewardToken) external {
    if (!isRewardToken[_rewardToken]) revert MustBeRewardToken();
    _provisionReward(_amountReward, _rewardToken);
}
``` [2](#0-1) 

The same unrestricted pattern exists in `BaseRewardPoolV2.sol` [3](#0-2)  and `mWOMSVBaseRewarder.sol` [4](#0-3) , all sharing the same `_provisionReward` division by the instantaneous `totalStaked()`.

Unlike `queueNewRewards()`, which is `onlyManager`-gated [5](#0-4) , `donateRewards()` has no such restriction — any wallet can call it. Because `rewardPerTokenStored` is a single global accumulator updated atomically for the entire pool the moment the donation lands, a staker's share of the newly-added reward depends only on their `balanceOf(_account)` at that exact moment, with no vesting or time-weighting:
```solidity
function earned(address _account, address _rewardToken) ... {
    return ((balanceOf(_account) *
        (rewardPerToken(_rewardToken) - userRewardPerTokenPaid[_rewardToken][_account])) /
        (10**stakingDecimals())) + userRewards[_rewardToken][_account];
}
``` [6](#0-5) 

`MasterMagpie.deposit()` / `withdraw()` are unrestricted, unlocked, and have no cooldown or exit fee — a user can deposit and withdraw in the same block:
```solidity
function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
    _deposit(_stakingToken, msg.sender, _amount, false);
}
function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
    _withdraw(_stakingToken, msg.sender, _amount, false);
}
``` [7](#0-6) 

This lets an attacker front-run any `donateRewards()` call (whether initiated by the protocol's own fee-distribution flow, e.g. `WombatBribeManager`/fee routing, or by any third party) with a large `deposit()`, let the donation land and inflate `rewardPerTokenStored` for the whole pool including the attacker's freshly deposited stake, then immediately `withdraw()` (or `getReward`) to lock in a share of the donated rewards proportional only to their capital, not to time staked — diluting genuine long-term stakers who receive less than they otherwise would have, and extracting value risk-free.

### Impact Explanation
This is a direct theft of yield mechanism: legitimate long-term stakers' proportional claim on donated/queued rewards is diluted by an attacker who has zero time-at-risk in the pool. Because the reward accounting is a single global `rewardPerTokenStored` value with no streaming/vesting over time (unlike typical `rewardRate`-over-`periodFinish` designs), any sizeable `donateRewards()` (or `queueNewRewards()` call by a manager routing protocol fees, which is a normal, expected operational flow) is immediately and fully sandwichable. This satisfies "theft of unclaimed yield" belonging to other stakers.

### Likelihood Explanation
Likelihood is high: `donateRewards()` requires no permission at all, `deposit`/`withdraw` in `MasterMagpie` have no lockup or fee, and reward-token donations/queuing (via fee routing from `WombatStaking._sendRewards` → `queueNewRewards`, or manual `donateRewards`) are a routine, frequent, and often sizeable part of protocol operation, making this attack readily and repeatedly exploitable by any MEV-capable wallet monitoring the mempool.

### Recommendation
- Time-weight reward distribution (e.g., stream donated/queued rewards over a period via a `rewardRate`/`periodFinish` model as in standard `StakingRewards`) instead of instantly folding the full amount into `rewardPerTokenStored`.
- Alternatively, gate `donateRewards()` behind `onlyManager` (matching `queueNewRewards()`), and/or introduce a minimum staking duration / withdrawal cooldown in `MasterMagpie` to prevent same-block or near-instant deposit→reward-realization→withdraw sequences.

### Proof of Concept
1. Pool `P` has `totalStaked() = 100e18` and `rewardPerTokenStored = R0` for reward token `T`.
2. Attacker observes a pending `donateRewards(1000e18, T)` (or a manager `queueNewRewards` fee-routing tx) in the mempool.
3. Attacker front-runs with `MasterMagpie.deposit(P, 900e18)`, becoming ~90% of `totalStaked()` (=1000e18).
4. The donation tx executes: `rewardPerTokenStored += 1000e18 * 1e_dec / 1000e18`, uniformly crediting all current stakers, including the attacker's freshly-deposited 900e18.
5. Attacker back-runs with `getReward()`/`withdraw()`, claiming ~900e18/1000e18 ≈ 90% of the donated 1000e18 reward tokens despite having staked for less than one block, at the expense of the pre-existing long-term stakers who should have received the full amount.

### Citations

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

**File:** rewards/BaseRewardPool.sol (L261-274)
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

        _provisionReward(_amountReward, _rewardToken);
        return true;
    }
```

**File:** rewards/BaseRewardPool.sol (L276-284)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
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

**File:** rewards/BaseRewardPoolV2.sol (L252-260)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/mWOMSVBaseRewarder.sol (L293-301)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only possible to donate already registered token
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    
```

**File:** rewards/MasterMagpie.sol (L334-346)
```text
    /// @notice Deposits staking token to the pool, updates pool and distributes rewards
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit to the pool
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }
```
