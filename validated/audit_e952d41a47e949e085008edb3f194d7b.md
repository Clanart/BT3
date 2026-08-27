### Title
Reward sniping via frontrunning `harvest()`/`queueNewRewards` with instant deposit-then-withdraw — ([File: rewards/BaseRewardPoolV2.sol], [File: wombat/WombatStaking.sol], [File: rewards/MasterMagpie.sol])

### Summary
`MasterMagpie` and the `BaseRewardPool`/`BaseRewardPoolV2` reward accounting apply newly harvested rewards instantly and proportionally to `totalStaked()` at the moment `queueNewRewards` executes, with no deposit cooldown or time-weighting on `deposit()`/`withdraw()`. This lets an unprivileged wallet front-run (or simply bundle in one transaction, since `harvest()` is permissionless) a large deposit immediately before a reward-harvest call, then withdraw immediately after, capturing a disproportionate share of rewards that were actually earned by long-term stakers — the same "instant-revaluation" bug class as the reported `notifyLoss`/redeem frontrun, but exploited on the reward-injection side to steal yield instead of avoiding a loss.

### Finding Description
`WombatStaking.harvest()` is callable by anyone (`_onlyActivePool` only checks the pool is active, no access control) and internally calls `_toMasterWomAndSendReward` → `_sendRewards` → `queueNewRewards` on the LP's rewarder: [1](#0-0) [2](#0-1) 

`queueNewRewards`/`_provisionReward` immediately folds the entire incoming reward amount into `rewardPerTokenStored` based on `totalStaked()` at that exact moment — there is no vesting, streaming, or time-weighting: [3](#0-2) 

Meanwhile, `MasterMagpie.deposit()`/`withdraw()` (and the pool-helper equivalents) have no cooldown or lock — a user can deposit and withdraw in immediate succession: [4](#0-3) [5](#0-4) 

Because `harvest()` is public and permissionless, an attacker does not even need to win a mempool race: they can call `deposit` → `harvest` → `withdraw` atomically (or across two blocks) to inject fresh capital right before the accumulated WOM/bonus rewards are folded into `rewardPerTokenStored`, then immediately exit. This dilutes the share of rewards that rightfully belongs to stakers who held their position over the accrual period, transferring pro-rata yield to the attacker with zero time-at-risk. This mirrors the root cause of the `notifyLoss` finding: an instantaneous, non-time-weighted change to per-share accounting that can be gamed by wrapping a deposit/withdraw around the state-changing event.

### Impact Explanation
This results in a direct, repeatable theft of unclaimed yield from legitimate long-term LPs/stakers in every pool routed through `WombatStaking`/`MasterMagpie` reward pools (`BaseRewardPool`, `BaseRewardPoolV2`). Each harvest cycle is an opportunity for capital-rich actors to skim a share of the reward pot without bearing exposure, and this can be repeated indefinitely and automated by a bot, since `harvest()` requires no privilege.

### Likelihood Explanation
High. `harvest()` is unauthenticated and can be triggered by anyone (including the attacker themselves), deposit/withdraw have no cooldown, and reward tokens (WOM/bonus tokens) accumulate over time creating a growing incentive to snipe each harvest. No governance, oracle, or privileged role is needed — purely an ordinary-wallet transaction sequence.

### Recommendation
Introduce one or more standard MEV-resistant mitigations for reward accrual, analogous to those proposed in the referenced report: (1) stream/vest newly queued rewards over a minimum window instead of crediting them instantly to `rewardPerTokenStored`, (2) add a minimum staking duration or deposit cooldown before a user's stake counts toward `totalStaked()` for reward purposes, or (3) restrict `harvest()`/`queueNewRewards` triggering to a trusted keeper using private relay, combined with per-block deposit/withdraw restrictions to prevent same-block or immediate-sequence sniping.

### Proof of Concept
1. Attacker observes (or is aware) that `WombatStaking` accumulates unharvested WOM/bonus rewards for a pool over time.
2. Attacker calls `MasterMagpie.deposit(stakingToken, largeAmount)` (via the pool helper) — this is unrestricted and immediately increases `totalStaked()` in the corresponding `BaseRewardPoolV2`: [6](#0-5) 
3. Attacker (or anyone) calls `WombatStaking.harvest(lpToken)`, which pulls the accrued WOM reward and calls `queueNewRewards`, instantly recalculating `rewardPerTokenStored` using the now-inflated `totalStaked()`: [1](#0-0) [7](#0-6) 
4. Attacker immediately calls `withdraw()`, which harvests the newly accrued reward share for their large, momentarily-staked position before unstaking: [8](#0-7) 
5. The attacker walks away with a share of rewards proportional to capital deployed for a few seconds, diluting the rewards long-term stakers should have received for the full accrual period.

### Citations

**File:** wombat/WombatStaking.sol (L329-335)
```text
    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L757-769)
```text
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
                    }
                }
            }
        }

        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
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

**File:** rewards/MasterMagpie.sol (L337-346)
```text
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

**File:** rewards/MasterMagpie.sol (L481-534)
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
