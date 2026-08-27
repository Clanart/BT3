### Title
First-staker-after-drain reward skimming via unrestricted `donateRewards` flush - ([File: rewards/BaseRewardPool.sol])

### Summary
`_provisionReward` in `BaseRewardPool.sol`/`BaseRewardPoolV2.sol` diverts the entire incoming reward into `rewardInfo.queuedRewards` whenever `totalStaked() == 0`, and later flushes all of `queuedRewards` into `rewardPerTokenStored` based on whoever is staked at the *next* call to `_provisionReward`. Because `donateRewards` is a public, unpermissioned function that anyone can call, an attacker who observes `totalStaked() == 0` can stake a trivial amount and immediately call `donateRewards` with a negligible amount to force-flush the queued rewards entirely to themselves.

### Finding Description
`_provisionReward` behaves as: [1](#0-0) 

If `totalStaked() == 0` (read from `IERC20(stakingToken).balanceOf(operator)` i.e. `MasterMagpie`'s token balance) at the time a reward is provisioned, the whole `_amountReward` accumulates in `rewardInfo.queuedRewards` instead of being distributed via `rewardPerTokenStored`. [2](#0-1) 

The next time `_provisionReward` runs while `totalStaked() > 0`, the entirety of `queuedRewards` (plus the new incoming amount) is divided by the *current* `totalStaked()` and added to `rewardPerTokenStored` in one shot — this is standard `MasterChef`-style share accounting, where whoever is staked at that instant captures the reward proportionally to their current balance, with no reference to who contributed stake while the reward was accruing.

Critically, `donateRewards` — which triggers the same `_provisionReward` flush logic — has no access control: [3](#0-2) 

whereas `queueNewRewards` is restricted to `onlyManager`: [4](#0-3) [5](#0-4) 

Because `donateRewards` is callable by anyone, an attacker does not need to wait for or predict when the privileged manager calls `queueNewRewards`. As soon as the attacker observes (or engineers, if they happen to be the pool's only real depositor) a moment when `totalStaked() == 0` and a nonzero `queuedRewards` balance exists (accrued from any prior legitimate `queueNewRewards`/`donateRewards` call while stake was zero), the attacker can:
1. Stake 1 wei via `MasterMagpie.deposit` (unrestricted, `whenNotPaused`/`nonReentrant` only). [6](#0-5) 
2. Call `donateRewards(1, rewardToken)` on the rewarder themselves, forcing `_provisionReward` to run with `totalStaked() == 1` (their own wei), which computes `rewardPerTokenStored += (queuedRewards + 1) * 10**decimals / 1`, i.e. effectively the entire queued reward attributed to their single wei of stake.
3. Call `getReward`/`multiclaim` on `MasterMagpie` to withdraw the full flushed reward via `earned()`. [7](#0-6) [8](#0-7) 

No modifier, cooldown, minimum-stake-duration, or reward-index checkpoint (e.g., a per-block/per-epoch snapshot of contributors) exists to prevent this: `earned()` is purely a function of current `balanceOf` and the global `rewardPerTokenStored`, which is unaware of historical stake distribution during the period the reward was earned.

### Impact Explanation
This allows theft of previously-queued (unclaimed) yield that rightfully belongs to future/returning stakers, redirected entirely to an attacker who stakes a trivial amount at the right moment and self-triggers the flush via the unpermissioned `donateRewards`. This matches the Immunefi "theft of unclaimed yield" impact class. The magnitude is bounded by however much reward accumulated in `queuedRewards` while `totalStaked()` was zero (which itself is bounded by protocol reward emission rates and how long the pool stays fully unstaked), so this is not full protocol insolvency, but it is a concrete, direct diversion of reward-token funds away from legitimate depositors.

### Likelihood Explanation
Exploitability is conditioned entirely on `totalStaked() == 0` occurring (or, more concerning, being achievable by an attacker who is the sole/majority staker in a low-TVL pool, e.g., a newly created pool or one experiencing a mass-withdrawal event) at a time when `queuedRewards > 0`. This is a real but non-guaranteed precondition — it cannot be forced against an established, actively-staked pool with many independent depositors, since the attacker cannot compel other users' stakes to zero out simultaneously. It is most feasible for small/new pools or immediately after `createRewarder`/`add` is used to launch a pool before meaningful TVL accrues, or after abnormal market conditions cause a temporary full unstake. Once the precondition is met, execution requires only capital of 1 wei of stake + 1 wei (or any small amount) of the reward token for `donateRewards`, is fully deterministic, and repeatable each time `totalStaked()` returns to zero with a nonzero `queuedRewards`.

### Recommendation
- Restrict `donateRewards` to trusted roles (e.g., `onlyManager`) so an attacker cannot self-trigger the queued-reward flush, or
- Change the flush mechanism so that `queuedRewards` is not attributed retroactively to whoever happens to be the very next staker; e.g., snapshot `queuedRewards` distribution against a minimum stake duration/checkpoint, or require a minimum elapsed time or minimum total-staked threshold before flushing, or distribute queued rewards pro-rata based on a time-weighted stake accumulator rather than instantaneous balance at flush time.

### Proof of Concept
Foundry test plan (`BaseRewardPool`/`MasterMagpie` integration):
1. Deploy `MasterMagpie`, register a pool with `BaseRewardPoolV2` as rewarder for staking token `LP` and reward token `RWD`, seed a normal user Alice with an initial stake (e.g., 100e18 `LP`).
2. Manager calls `queueNewRewards(1000e18, RWD)` while Alice is staked — verify it distributes normally via `rewardPerTokenStored`.
3. Alice fully withdraws (`MasterMagpie.withdraw`), driving `totalStaked() == 0`.
4. Manager (or any caller with reward tokens) triggers another reward provisioning event, e.g. `queueNewRewards(500e18, RWD)`, while `totalStaked() == 0` — assert `rewards[RWD].queuedRewards == 500e18` and `rewardPerTokenStored` unchanged.
5. Attacker (Bob, unprivileged EOA) calls `MasterMagpie.deposit(LP, 1)` (1 wei stake).
6. Bob calls `donateRewards(1, RWD)` on the rewarder (unpermissioned call) — assert this flushes `queuedRewards` into `rewardPerTokenStored` with `totalStaked() == 1`.
7. Bob calls `MasterMagpie.multiclaim`/rewarder `getReward` — assert Bob receives ~500e18 `RWD` (minus rounding), i.e., nearly the entire queued reward, despite having contributed 1 wei of stake for effectively zero time, while Alice (the actual prior contributor during reward accrual) receives nothing from this queued batch.
8. Assert `rewards[RWD].queuedRewards == 0` after step 6, confirming full capture by Bob.

### Citations

**File:** rewards/BaseRewardPool.sol (L93-97)
```text
    modifier onlyManager() {
        if (!managers[msg.sender])
            revert OnlyManager();
        _;
    }
```

**File:** rewards/BaseRewardPool.sol (L126-128)
```text
    function totalStaked() external override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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

**File:** rewards/BaseRewardPool.sol (L221-240)
```text
    function getReward(address _account, address _receiver)
        override
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
                userRewards[rewardToken][_account] = 0;
                IERC20(rewardToken).safeTransfer(_receiver, reward);
                emit RewardPaid(_account, _receiver, reward, rewardToken);
            }
        }

        return true;
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

**File:** rewards/MasterMagpie.sol (L337-339)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }
```
