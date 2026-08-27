### Title
Permissionless `donateRewards` lets an attacker force-flush `queuedRewards` against a self-inflated, transient stake — theft of unclaimed yield - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`donateRewards` is callable by anyone and simply forwards to `_provisionReward`, which — whenever `totalStaked() != 0` — folds the *entire* accumulated `queuedRewards` backlog into `rewardPerTokenStored` divided by the stake balance read at that exact block. An attacker can inflate `totalStaked()` momentarily (deposit right before calling `donateRewards` with 1 wei), force the backlog to be distributed pro‑rata against their own transient stake, then withdraw, capturing reward that should have accrued to the genuine long‑term stakers who were present while the backlog built up.

### Finding Description
`totalStaked()` is defined as a live read of `IERC20(stakingToken).balanceOf(operator)` [1](#0-0) , so it always mirrors the current staking balance by construction — there is no separable "reconciliation" invariant to violate there. The actual exploitable defect is in `_provisionReward`: [2](#0-1) 

When `totalStaked() == 0`, incoming rewards just accumulate in `rewardInfo.queuedRewards` (e.g. rewards seeded before any staker exists, or after all stakers have withdrawn). The very next call to `_provisionReward` — from *anyone*, since `donateRewards` has no access control beyond `isRewardToken[_rewardToken]` [3](#0-2)  — folds the whole backlog plus the new `_amountReward` (attacker-chosen, down to 1 wei) into `rewardPerTokenStored`, dividing by `totalStaked()` **at that instant**.

Because `MasterMagpie.deposit`/`withdraw` have no cooldown, lock, or minimum holding period [4](#0-3) , an attacker can, within one transaction or block:
1. Deposit a large amount of `stakingToken` into `MasterMagpie` (own capital or via a flash-loan-funded position), dramatically increasing `totalStaked()`.
2. Call `donateRewards(1, _rewardToken)` on the reward pool while their deposit dominates `totalStaked()`, forcing the whole `queuedRewards` backlog to be folded into `rewardPerTokenStored` at that skewed ratio.
3. Immediately withdraw via `MasterMagpie.withdraw`, which triggers `_harvestBaseRewarder` → `getReward`, crediting the attacker with their inflated share of `_earned()` before their balance decreases.

This lets the attacker intercept reward that legitimate long-standing stakers earned while the backlog accumulated, since `_provisionReward` performs a single point-in-time distribution with no time-weighting, vesting, or snapshot tied to the stake set that actually existed while the backlog was pending.

### Impact Explanation
This is a theft-of-unclaimed-yield vector: genuine stakers who held positions while `queuedRewards` accumulated are diluted or fully deprived of their fair share once an attacker times a permissionless `donateRewards(1 wei, token)` call against a transient, self-inflated stake balance. Matches Immunefi "High – Theft of unclaimed yield."

### Likelihood Explanation
The attack requires two preconditions to line up: (1) a non-zero `queuedRewards` backlog for some registered `_rewardToken` (only accrues while `totalStaked() == 0`, e.g. before the first depositor or after a full pool exit), and (2) the attacker's ability to transiently dominate `totalStaked()` (via capital or a flash loan of the staking token / its underlying LP components) within one block/transaction. `donateRewards` itself is trivially callable with 1 wei and no permission, so once the backlog precondition exists, the timing is entirely attacker-controlled and repeatable each time a backlog reappears (e.g., every time the pool fully drains to zero stake and reward flow continues to be queued).

### Recommendation
- Time-weight or checkpoint reward distribution instead of dividing the full backlog by the instantaneous `totalStaked()` (e.g., stream `queuedRewards` linearly over time as in standard `StakingRewards`/Synthetix designs, or snapshot eligible stakers before flushing).
- Consider gating `donateRewards`/backlog-flush timing (e.g., minimum stake duration, or restrict who can trigger the flush of a pre-existing backlog to `onlyManager`), while still allowing permissionless straightforward reward top-ups that don't touch stale `queuedRewards`.

### Proof of Concept
Hardhat plan:
1. Deploy `MasterMagpie`, `BaseRewardPoolV2` for a mock `stakingToken` and a registered `rewardToken`.
2. Ensure `totalStaked() == 0` (no depositors yet) and have the reward manager call `queueNewRewards(1000e18, rewardToken)` — confirm it lands fully in `rewards[rewardToken].queuedRewards` since `totalStaked() == 0`.
3. Have a normal user, `Alice`, deposit `100e18` `stakingToken` via `MasterMagpie.deposit`.
4. In the same block, have attacker `Mallory` deposit `1_000_000e18` `stakingToken` (representing flash-loaned/borrowed capital), then immediately call `BaseRewardPoolV2.donateRewards(1, rewardToken)`.
5. Assert `rewardPerTokenStored` for `rewardToken` jumped based on `totalStaked() ≈ 1_000_100e18` dominated by Mallory's deposit.
6. Have Mallory call `MasterMagpie.withdraw` immediately, triggering `getReward`, and assert Mallory receives the overwhelming majority of the 1000e18 backlog reward while Alice's `earned(rewardToken)` is negligible relative to her actual staking duration/share, demonstrating the yield theft.

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L126-128)
```text
    function totalStaked() public override virtual view returns (uint256) {
        return IERC20(stakingToken).balanceOf(operator);
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
