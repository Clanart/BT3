### Title
Just-in-time reward sniping via instantaneous, non-time-weighted `rewardPerToken` accounting in `BribeRewardPool`/`BaseRewardPoolV2` - ([File: rewards/BaseRewardPoolV2.sol, rewards/BribeRewardPool.sol])

### Summary
`BaseRewardPoolV2._provisionReward` applies newly queued bribe rewards instantly against the current `totalStaked()`/`totalSupply`, and `stakeFor`/`withdrawFor` snapshot `userRewardPerTokenPaid` only at call time with no minimum holding period. An unprivileged staker can therefore deposit immediately before a bribe harvest (`queueNewRewards`) and withdraw with `claim=true` immediately after in the same block/transaction, capturing a full pro-rata share of the reward drop with zero time-at-risk, diluting the share that pre-existing, long-term stakers receive from that same distribution.

### Finding Description
`BribeRewardPool.stakeFor` and `withdrawFor` are gated by `onlyOperator` [1](#0-0) , but the operator is `WombatStaking`/`MasterMagpie`, which any unprivileged user reaches via ordinary deposit/withdraw calls — so the attacker does not need any special role, only ordinary token custody.

`stakeFor` and `withdrawFor` both use the `updateRewards(_for, rewardTokens)` modifier, which snapshots `userRewards`/`userRewardPerTokenPaid` using `balanceOf(_account)` at the moment of the call: [2](#0-1) .

The core issue is in `_provisionReward`, which folds a newly queued reward into `rewardPerTokenStored` instantly, proportionally to whatever `totalStaked()` is at that exact instant: [3](#0-2) . There is no time-decay/streaming of rewards and no minimum-holding-period check anywhere in `stakeFor`/`withdrawFor` [4](#0-3) .

`_earned` then pays out based purely on the instantaneous `_userShare` and the paid/stored `rewardPerToken` delta, with no notion of duration held: [5](#0-4) .

Exploit flow:
1. Attacker (a normal WombatStaking/BribeRewardPool depositor, unprivileged) calls deposit, which routes into `BribeRewardPool.stakeFor(attacker, largeAmount)`. The `updateRewards` modifier runs first and sets `userRewardPerTokenPaid[token][attacker] = rewardPerToken(token)` (the pre-harvest value), then `_balances[attacker]` and `totalSupply` are inflated.
2. In the same block/tx, a bribe harvest calls `queueNewRewards`, which is `onlyManager` [6](#0-5)  — this step itself requires the harvest/manager path to execute in the same block as the attacker's deposit (a keeper- or protocol-triggered harvest, not attacker-privileged), consistent with the stated preconditions. `_provisionReward` bumps `rewardPerTokenStored` using `totalStaked()`, which now includes the attacker's freshly inflated balance.
3. Attacker calls withdraw, routing into `withdrawFor(attacker, largeAmount, true)`. The `updateRewards` modifier computes `userShare = balanceOf(attacker)` (still the large pre-withdrawal balance) and computes `_earned` using the now-increased `rewardPerToken(token)` minus the pre-harvest `userRewardPerTokenPaid`, crediting the attacker a full pro-rata share of the entire reward drop despite holding stake for zero economic time. `_getReward` then pays it out immediately.

None of the existing guards (`onlyOperator`, `onlyManager`, `updateRewards`) prevent this because they correctly gate *who* can call the state-changing functions, but they do not address *when relative to reward injection* a stake is opened/closed — there is no time-weighted-average-balance, no vesting/streaming of rewards over a duration, and no cooldown between stake and unstake.

### Impact Explanation
This is a theft of unclaimed bribe yield: the attacker extracts reward tokens proportional to a balance that existed only for an instant, funded from the same finite reward pot that pre-existing long-term stakers were entitled to. Every unit of reward the attacker captures this way is a unit long-term stakers do not receive from that harvest, a direct value transfer that matches the "theft of unclaimed yield" impact class. Because `BribeRewardPool` holds real bribe reward tokens transferred in via `_provisionReward`/`safeTransferFrom`, this is a concrete, quantifiable loss of principal-equivalent yield, not merely a griefing/DoS or unbounded-loop issue.

### Likelihood Explanation
Feasibility depends on the attacker (or an accomplice/keeper interaction they can trigger or predict) being able to land a deposit before, and a withdrawal after, the bribe-harvest transaction within the same block — this is realistic via mempool front-running/back-running or bundling if the harvest call is externally callable/predictable, and capital can be sized to whatever LP/staking token the attacker can acquire (potentially large, since no cap exists on `_balances`). It is repeatable every time a bribe harvest occurs and requires no elevated privileges, only ordinary staking-token custody for the duration of one block.

### Recommendation
Introduce time-weighting or a minimum holding period before rewards can be claimed/withdrawn, e.g.:
- Stream newly queued rewards over a fixed duration (rewardRate/periodFinish pattern à la Synthetix `StakingRewards`) instead of instantly folding the full amount into `rewardPerTokenStored`, so a same-block depositor can only accrue a negligible sliver of the reward.
- And/or enforce a minimum staking duration (cooldown) in `BribeRewardPool`/`BaseRewardPoolV2` before `withdrawFor(..., claim=true)` can realize rewards accrued from a `queueNewRewards` that occurred after the stake began.

### Proof of Concept
Foundry test plan:
1. Deploy `BribeRewardPool` with a mock `operator` (acting as `WombatStaking`) and register a reward token; seed an existing long-term staker (`alice`) with a stake via `stakeFor(alice, X)` in an earlier block.
2. In a single block/transaction: call `stakeFor(attacker, Y)` with `Y >> X`, then as the reward manager call `queueNewRewards(rewardAmount, token)`, then call `withdrawFor(attacker, Y, true)`.
3. Assert `attacker`'s realized `RewardPaid` amount ≈ `rewardAmount * Y / (X + Y)` (full pro-rata instantaneous share) despite zero holding duration.
4. Compare against `alice`'s later `earned(alice, token)`, showing her time-weighted share of `rewardAmount` was reduced below what it would have been absent the attacker's JIT stake (i.e., `rewardAmount * X / (X + Y)` instead of `rewardAmount` had attacker not participated).
5. Assert conservation is violated from a time-weighted-average-balance perspective: attacker's realized reward is not bounded by any time-integral of stake, only by instantaneous balance at the `queueNewRewards` call.

### Citations

**File:** rewards/BribeRewardPool.sol (L57-85)
```text
    function stakeFor(address _for, uint256 _amount)
        external
        virtual
        onlyOperator
        updateRewards(_for, rewardTokens)
    {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;

        emit Staked(_for, _amount);
    }

    /// @notice Updates informaiton for a user in case of a withdraw. Can only be called by the Masterchief operator
    /// @param _for Address account
    /// @param _amount Amount of withdrawed tokens by the user on masterchief
    function withdrawFor(
        address _for,
        uint256 _amount,
        bool claim
    ) external virtual onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;

        emit Withdrawn(_for, _amount);

        if (claim) {
            _getReward(_for);
        }
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L107-120)
```text
    modifier updateRewards(address _account, address[] memory _rewards) {
        uint256 length = _rewards.length;
        uint256 userShare = balanceOf(_account);
        
        for (uint256 index = 0; index < length; ++index) {
            address rewardToken = _rewards[index];
            // if a reward stopped queuing, no need to recalculate to save gas fee
            if (userRewardPerTokenPaid[rewardToken][_account] == rewardPerToken(rewardToken))
                continue;
            userRewards[rewardToken][_account] = _earned(_account, rewardToken, userShare);
            userRewardPerTokenPaid[rewardToken][_account] = rewardPerToken(rewardToken);
        }
        _;
    }    
```

**File:** rewards/BaseRewardPoolV2.sol (L270-286)
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
