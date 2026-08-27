### Title
`donateRewards` allows a free (zero-cost), permissionless trigger of `queuedRewards` flush, letting a sole/majority staker capture the entire dormant-period reward pool - ([File: rewards/mWOMSVBaseRewarder.sol])

### Summary
`mWOMSVBaseRewarder._provisionReward` accumulates donated/queued rewards into `Reward.queuedRewards` whenever `totalStaked() == 0`, and flushes that entire balance into `rewardPerTokenStored` the instant `totalStaked() > 0`, split only by the current `totalStaked()` at that moment. Because `donateRewards` is unauthenticated and does not enforce `_amountReward > 0`, any attacker can become the (near) sole staker and call `donateRewards(0, _rewardToken)` at zero cost to force-flush the entire dormant `queuedRewards` balance into `rewardPerTokenStored`, capturing rewards accrued during a period when they were not staked.

### Finding Description
`_provisionReward` in `rewards/mWOMSVBaseRewarder.sol` (lines 305-328) has the following logic: [1](#0-0) 

- While `totalStaked() == 0`, every reward provisioned (via `queueNewRewards` from a manager, or via `donateRewards` from anyone) is appended to `rewardInfo.queuedRewards` without being attributed to any staker.
- The moment `totalStaked() > 0`, the entire `queuedRewards` balance is added to the new `_amountReward` and divided by the *current* `totalStaked()`, updating `rewardPerTokenStored` in a single step. There is no time-weighting of the dormant period — whoever holds stake at that exact call captures the full flush pro-rata to their share, with zero regard for how long they've actually been staked.
- `donateRewards` has no `_amountReward > 0` check: [2](#0-1) 
This means an attacker can call `donateRewards(0, _rewardToken)` for a token already in `isRewardToken`. `IERC20Metadata.safeTransferFrom(msg.sender, address(this), 0)` succeeds as a no-op on standard/compliant ERC20s, so the call costs nothing beyond gas, yet it still executes the `totalStaked() > 0` branch and flushes 100% of `queuedRewards` into `rewardPerTokenStored`.
- `balanceOf` and reward accounting rely on `MasterMagpie.stakingInfo`, which reflects the attacker's staked amount immediately once locked/staked — there is no minimum holding period gating reward eligibility, only `_calExpireForfeit`-style checks on claim which are unrelated to this timing issue.
- No existing modifier (`onlyManager`, `nonReentrant`, `whenNotPaused`) prevents this: `donateRewards` is intentionally public and unauthenticated, and the flush condition only checks `totalStaked() == 0`, not who or when the caller became the majority/sole staker.

### Impact Explanation
An attacker who becomes the majority or sole staker of `mWOMSV` at a moment when `Reward.queuedRewards` has built up over a dormant multi-epoch period (e.g., after long inactivity, or shortly after deployment before other stakers arrive) can trigger `donateRewards(0, _rewardToken)` for free and instantly claim via `getReward`/`getRewards` (routed through `masterMagpie`), capturing yield that was contributed by/intended for the whole staker set across the dormant period. This is theft of unclaimed yield and effectively front-runs the arrival of genuine long-term stakers — falling under "theft or permanent freezing of unclaimed yield."

### Likelihood Explanation
- Preconditions: the attacker needs the reward token to already be registered (`isRewardToken[_rewardToken] == true`), a non-trivial `queuedRewards` balance accumulated while `totalStaked() == 0`, and the ability to be the dominant staker at the flush moment (feasible right after deployment, or after a period of full unstaking/dormancy, since staking mWOMSV only requires locking mWOM via `mWomSV.lock`/`lockFor`, which appears to register stake in `MasterMagpie` immediately).
- Capital needed: minimal — a small/1-wei-scale mWOM lock is sufficient to be "sole staker," and the trigger call itself (`donateRewards(0, token)`) costs no reward-token capital.
- This is fully repeatable each time the pool goes dormant (`totalStaked()` returns to 0, e.g., all stakers fully unlock) and rewards are queued again.
- Uncertainty: I was unable to fully confirm within the available context whether `mWomSV.lock`/`lockFor` and `MasterMagpie`'s deposit path impose any minimum lock duration or vesting delay before `stakingInfo` reflects a stake usable for reward eligibility; I recommend verifying this in a full Devin session with complete file access, as it affects exact feasibility/timing of the front-run.

### Recommendation
- Require `_amountReward > 0` in `donateRewards` (and `queueNewRewards`) to remove the free-trigger vector.
- Redesign reward flushing to be time-weighted rather than instantaneous: e.g., stream `queuedRewards` over a duration (reward-rate style, as in Synthetix `StakingRewards`) instead of crediting the full dormant-period balance to whoever is staked at the flush block.
- Alternatively, snapshot/checkpoint `totalStaked()` and reward accrual per-block so that a staker only accrues rewards proportional to the time they were actually staked, rather than capturing a lump-sum flush attributable to periods before they staked.

### Proof of Concept
Foundry test plan:
1. Deploy `mWOMSVBaseRewarder`, `mWomSV`, `MasterMagpie`, and a reward ERC20; register the reward token via `queueNewRewards` once (as manager) to add it to `rewardTokens`/`isRewardToken`.
2. Ensure `totalStaked() == 0` (no one has locked `mWOM`/`mWomSV` yet).
3. Call `queueNewRewards`/`donateRewards` multiple times with nonzero amounts while `totalStaked() == 0`, accumulating `rewards[token].queuedRewards` across several "epochs" (assert `queuedRewards` grows, `rewardPerTokenStored` stays 0).
4. Have `ATTACKER` lock a minimal amount of `mWOM` (e.g., 1 wei-equivalent) via `mWomSV.lock`, becoming the sole entry reflected in `MasterMagpie.stakingInfo`.
5. `ATTACKER` calls `donateRewards(0, rewardToken)` — assert this succeeds with `safeTransferFrom` of amount 0, and that `rewardPerTokenStored` jumps to reflect the entire prior `queuedRewards` divided by `totalStaked()` (now equal to attacker's tiny stake).
6. `ATTACKER` calls `getReward`/`getRewards` through `masterMagpie` and assert they receive (approximately) 100% of the dormant-period reward token balance that was donated by others, despite having staked for 0 elapsed time.
7. Assert this reward amount is disproportionate to any reasonable pro-rata share based on staking duration versus the dormant accumulation period.

### Citations

**File:** rewards/mWOMSVBaseRewarder.sol (L296-301)
```text
    function donateRewards(uint256 _amountReward, address _rewardToken) external {
        if (!isRewardToken[_rewardToken])
            revert MustBeRewardToken();

        _provisionReward(_amountReward, _rewardToken);
    }    
```

**File:** rewards/mWOMSVBaseRewarder.sol (L305-328)
```text
    function _provisionReward(uint256 _amountReward, address _rewardToken) internal {
        IERC20Metadata(_rewardToken).safeTransferFrom(
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
                (_amountReward * 10**mWOMSVDecimal) / totalStaked();
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```
