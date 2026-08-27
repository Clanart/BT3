### Title
Just-in-time deposit sniping of queued/donated rewards via instant snapshot-based `rewardPerTokenStored` update - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`BaseRewardPoolV2.donateRewards` / `_provisionReward` distributes rewards as a single instantaneous bump to `rewardPerTokenStored` based on `totalStaked()` at the exact moment the funding call executes, rather than streaming rewards linearly over time. Combined with the zero-stake `queuedRewards` carry-over branch, an attacker can accumulate a large pending reward while `totalStaked()==0`, then stake a large amount immediately before triggering the release, capturing a share of the reward proportional to their instantaneous balance rather than any time-weighted contribution.

### Finding Description
`_provisionReward` in `rewards/BaseRewardPoolV2.sol` (lines 290-314) behaves as follows: [1](#0-0) 
- If `totalStaked() == 0` at the time of the call, the donated amount is added to `rewardInfo.queuedRewards` and no accounting update occurs.
- On the *next* call to `_provisionReward` (via `donateRewards`, permissionlessly callable by anyone per [2](#0-1) , or `queueNewRewards`) when `totalStaked() > 0`, the entire accumulated `queuedRewards` is added to the new donation amount and `rewardPerTokenStored` is bumped by `(_amountReward * 10**decimals) / totalStaked()` using **whatever `totalStaked()` is at that exact block**.

Critically, this contract has no time-based streaming (no `rewardRate`/`periodFinish` accrual as in typical Synthetix/Convex-style linear vesting); the entire reward is applied as one instantaneous index bump. A user's earned share is `balanceOf(_account) * (rewardPerToken - userRewardPerTokenPaid) / 1e18`, computed via `_earned`/`earned` at [3](#0-2) , where `balanceOf` reads the *current* live stake from `MasterMagpie.stakingInfo` ( [4](#0-3) ), not a historical/time-weighted balance.

Exploit flow:
1. Attacker (or anyone) calls `donateRewards` while `totalStaked() == 0`, so the amount accumulates in `queuedRewards`.
2. A legitimate user deposits a small stake, making `totalStaked() > 0` but small.
3. Attacker deposits a large stake into `MasterMagpie` (dominating `totalStaked()`), then immediately calls `donateRewards` again with even a dust amount.
4. `_provisionReward` executes the `else` branch: `_amountReward += queuedRewards` (the entire carried-over pot), and `rewardPerTokenStored` is bumped using `totalStaked()` that is now dominated by the attacker's stake.
5. Attacker calls `getReward`, and since their `userRewardPerTokenPaid` was set to the pre-bump value at deposit time, `earned()` credits them `balanceOf(attacker) * bump`, i.e., nearly the entire queued reward, despite having zero exposure during the period the reward was queued/pending.

This is possible because there are no protections such as: minimum staking duration before reward eligibility, time-weighted reward accrual, or restrictions preventing `donateRewards` from being called by unprivileged accounts to force realization of `queuedRewards` at an attacker-chosen block.

### Impact Explanation
This allows theft of yield that should be distributed to long-term/legitimate stakers (or remain permanently queued/undistributed) by an unprivileged attacker who stakes only momentarily around the triggering transaction. This matches the "theft of unclaimed yield" impact class — the attacker extracts value from `queuedRewards` (and any concurrent donation) that other stakers/depositors have an economic claim to, diluting/stealing their expected share without ever bearing exposure during the period the reward accrued.

### Likelihood Explanation
The preconditions are fully attacker-controlled and require no special privilege:
- `donateRewards` is `external` with no access control beyond `isRewardToken` check [2](#0-1) , so any EOA can trigger both the queuing donation and the release donation.
- Depositing into `MasterMagpie` to become the dominant staker is a normal unprivileged action.
- The attack is capital-intensive only in that the attacker must temporarily hold a large stake at the moment of the second `donateRewards` call, but this can be done with a flash-loan-able asset if the staking token supports it, or simply with capital the attacker already possesses, and is repeatable across any reward token registered on the pool and across any pool using this same reward-accounting pattern (also present in `rewards/BaseRewardPool.sol`, `rewards/vlMGPBaseRewarder.sol`, `rewards/mWOMSVBaseRewarder.sol`).
- No `nonReentrant`, cooldown, or time-weighting guard exists in `_provisionReward` or the `updateReward`/`updateRewards` modifiers to prevent this.

### Recommendation
Replace the instantaneous "snapshot" reward release with a time-weighted/streaming distribution (e.g., `rewardRate` + `periodFinish` linear vesting as in standard Synthetix `StakingRewards`), so that `rewardPerToken()` accrues continuously based on elapsed time and historical `totalStaked()`, rather than jumping entirely at the moment of donation based on whoever is staked at that instant. At minimum, when releasing `queuedRewards`, distribute them over a vesting window rather than crediting them entirely to the current `totalStaked()` snapshot, and/or restrict `donateRewards` to trusted managers with rate-limiting to prevent adversarial timing of the release.

### Proof of Concept
Foundry test plan:
1. Deploy `BaseRewardPoolV2` with a mock staking token wired to a mock `MasterMagpie` and a mock ERC20 reward token; register the reward token.
2. With `totalStaked() == 0`, have `attacker` call `donateRewards(1000e18, rewardToken)`. Assert `rewards[rewardToken].queuedRewards == 1000e18` and `rewardPerTokenStored == 0`.
3. Have `victim` deposit `1e18` stake via `MasterMagpie`. Assert `totalStaked() == 1e18`.
4. Have `attacker` deposit `999e18` stake via `MasterMagpie` (dominating the pool), then immediately call `donateRewards(1, rewardToken)`.
5. Assert `rewards[rewardToken].queuedRewards == 0` and `rewardPerTokenStored` was bumped using `totalStaked() == 1000e18`.
6. Call `getReward(attacker, attacker)` and `getReward(victim, victim)`.
7. Assert `attacker`'s claimed reward ≈ `999/1000 * 1000e18` (nearly the entire queued pot) while `victim`, who was staked during and before the queuing/release window, only receives `1/1000 * 1000e18` — demonstrating that reward share is determined by instantaneous balance at release time, not by time-weighted exposure, violating expected conservation/fairness (attacker's captured share of the queued portion should be minimal/zero given they were unstaked during the entire accrual period, not their full post-deposit `balanceOf` share).

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L133-136)
```text
    function balanceOf(address _account) public override virtual view returns (uint256) {
        (uint256 staked, ) =  IMasterMagpie(operator).stakingInfo(stakingToken, _account);
        return staked;
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L177-184)
```text
    function earned(address _account, address _rewardToken)
        public
        override
        view
        returns (uint256)
    {
        return _earned(_account, _rewardToken, balanceOf(_account));
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L255-260)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }
```

**File:** rewards/BaseRewardPoolV2.sol (L301-313)
```text
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
