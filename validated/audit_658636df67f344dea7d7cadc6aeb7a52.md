### Title
Front-Runnable Reward Distribution in `BaseRewardPool._provisionReward` Enables Instant-Stake Reward Theft - (File: `rewards/BaseRewardPool.sol`)

### Summary
`BaseRewardPool` (and its `BaseRewardPoolV2` counterpart) distributes newly queued/donated rewards to *whatever* balance is staked at the exact block the reward is provisioned, with no check that the staked balance reflects genuine, at-risk liquidity providers rather than balance that was deposited moments earlier purely to intercept the reward. This mirrors the root cause of the reported Angstrom bug: reward/growth accounting is computed against a mutable, unauthenticated state variable (there: pool liquidity/tick; here: `totalStaked()`/`balanceOf`) with no validation that this state matches what was expected/committed to when the reward-triggering transaction was prepared, allowing an attacker to front-run the reward-provisioning transaction and capture a disproportionate share of rewards meant for honest, longer-term stakers.

### Finding Description
`_provisionReward` computes the new `rewardPerTokenStored` by dividing the incoming reward amount by `this.totalStaked()` evaluated at execution time: [1](#0-0) 

`totalStaked()` and `balanceOf()` simply read the current staking balances from `MasterMagpie` with no snapshotting, vesting, or minimum-holding-period requirement: [2](#0-1) 

Reward accrual for a user is `balanceOf(_account) * (rewardPerToken - userRewardPerTokenPaid[...]) / 10**decimals + userRewards[...]`, i.e., purely proportional to the balance held at the instant `rewardPerTokenStored` is bumped: [3](#0-2) 

Any ordinary wallet can observe a pending `queueNewRewards` (manager-triggered, e.g. from `WombatStaking` harvest) or a public `donateRewards` call in the mempool, and front-run it: (1) deposit a large amount of the staking token into `MasterMagpie` for that pool immediately before the reward-provisioning transaction lands, (2) let `_provisionReward` mint the entire `rewardPerTokenStored` bump against the now-inflated `totalStaked()` (which includes the attacker's flash-deposited balance), (3) immediately withdraw the deposit afterward. Because `earned()` only cares about the balance at the moment `rewardPerTokenStored` changes (captured via `userRewardPerTokenPaid`), the attacker earns a share of the reward proportional to their flash-deposited stake even though they bore none of the time-at-risk that legitimate LPs did. This is exactly analogous to the Angstrom report's core defect: reward math trusts an easily front-run, unvalidated on-chain state value (liquidity/tick there, `totalStaked()` here) instead of verifying it matches the state that was expected/intended when the reward transaction was constructed.

`donateRewards` in particular is fully permissionless and can be called by anyone, including the attacker themselves as part of the same block/bundle, making the "attacker triggers or co-times the reward event" step directly reachable without needing to race a manager's transaction: [4](#0-3) 

### Impact Explanation
This allows direct theft of yield from honest liquidity providers: an attacker with no genuine exposure to the staking token can capture a disproportionate slice of any reward injection (MGP emissions routed through `BaseRewardPool`/`BaseRewardPoolV2`, or bonus token distributions) by flash-staking around the `_provisionReward` call, diluting the `rewardPerTokenStored` share that should have accrued only to depositors who held their position across the relevant reward-accrual period. Since rewards are fungible tokens transferred out via `getReward`, this constitutes concrete theft of unclaimed yield belonging to other stakers, satisfying the "theft of unclaimed yield" impact bar.

### Likelihood Explanation
Likelihood is medium: it requires the attacker to have capital to flash-stake (deposit/withdraw of the staking token, no lock-up preventing this at the `BaseRewardPool` layer) and to time their deposit around a `queueNewRewards`/`donateRewards` call, which is either publicly visible in the mempool (front-runnable) or, in the `donateRewards` case, fully controllable by the attacker themselves (self-triggered, no front-running needed at all). No privileged role is required.

### Recommendation
Introduce a reward-accrual mechanism that is resistant to instantaneous balance changes around reward provisioning, e.g.:
- Time-weight reward eligibility (e.g., a minimum staking duration or linear vesting/streaming of newly queued rewards over a window, as in a `periodFinish`/`rewardRate` streaming model) instead of an instantaneous lump-sum bump to `rewardPerTokenStored`.
- Alternatively, snapshot `totalStaked()` prior to allowing new deposits to affect the current reward epoch, or require deposits to season for a minimum period before they count toward `balanceOf()` used in `earned()`.

### Proof of Concept
1. Attacker monitors mempool for a manager's `queueNewRewards(amount, rewardToken)` call on a `BaseRewardPool` (or simply prepares to call the permissionless `donateRewards`).
2. Attacker calls `MasterMagpie.deposit(stakingToken, largeAmount)` in the same block/prior transaction, inflating `totalStaked()`.
3. `_provisionReward` executes, computing `rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked()` — now diluted across the attacker's inflated stake.
4. Attacker calls `MasterMagpie.withdraw(stakingToken, largeAmount)` to exit their position.
5. Attacker calls `getReward` and receives a share of the reward proportional to their flash-staked balance, at the expense of pre-existing stakers who held their position through genuine risk exposure. [1](#0-0)

### Citations

**File:** rewards/BaseRewardPool.sol (L124-136)
```text
    /// @notice Returns current amount of staked tokens
    /// @return Returns current amount of staked tokens
    function totalStaked() external override virtual view returns (uint256) {
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

**File:** rewards/BaseRewardPool.sol (L297-320)
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
    }
```
