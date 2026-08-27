### Title
`donateRewards` lets any staker flush the entire `queuedRewards` backlog at a self-chosen stake distribution, stealing yield owed to prior/other stakers - ([File: rewards/BaseRewardPool.sol])

### Summary
`donateRewards(uint256 _amountReward, address _rewardToken)` is fully permissionless and calls `_provisionReward`, which — whenever `totalStaked() != 0` — immediately folds *all* of `rewards[_rewardToken].queuedRewards` into `rewardPerTokenStored`, divided by the staking-token balance that exists **at that exact block**. Because staking/unstaking and calling `donateRewards` are both unpermissioned actions available to any address, an attacker can pick the instant and the stake set against which a long-accumulated backlog is released, capturing reward that should have accrued to the historical stakers who were actually staked while the backlog built up.

### Finding Description
`_provisionReward` in [1](#0-0)  updates `historicalRewards` unconditionally by `_amountReward`, then branches on `this.totalStaked() == 0`:
- If `totalStaked() == 0`, the incoming reward is only queued (`queuedRewards += _amountReward`) — no `rewardPerTokenStored` update, so nobody accrues anything yet.
- If `totalStaked() != 0`, **any pre-existing `queuedRewards` is folded in wholesale** together with the new `_amountReward`, and `rewardPerTokenStored` is bumped by `(amount * 1e_stakingDecimals) / totalStaked()` — using whatever `totalStaked()` happens to be in that same transaction/block.

`donateRewards` at [2](#0-1)  has no access control beyond `isRewardToken[_rewardToken]` being true — unlike `queueNewRewards`, which is `onlyManager` ( [3](#0-2) ).

Exploit flow:
1. A backlog of `queuedRewards` accumulates for some reward token while `totalStaked() == 0` (e.g., before the first staker ever deposits into the pool, or after all stakers fully withdraw and the manager or anyone continues to queue/donate rewards during that gap).
2. An attacker, either the very first depositor or someone who re-stakes into a currently near-empty pool, deposits into `stakingToken` via `MasterMagpie`, making `totalStaked()` equal (almost) entirely their own balance.
3. In the same transaction (or immediately after), the attacker calls `donateRewards(1, _rewardToken)` on any already-registered reward token. Because `totalStaked() != 0` now, `_provisionReward` immediately folds the *entire* pre-existing `queuedRewards` backlog into `rewardPerTokenStored`, dividing by `totalStaked()`, which is essentially the attacker's own freshly-deposited balance.
4. The attacker's `userRewardPerTokenPaid` was recorded *before* this jump (at deposit time via `_updateFor`), so `earned()` credits them almost the full backlog even though they had zero staking duration corresponding to when that backlog was earned.
5. The attacker calls `getReward` to claim, then withdraws — realizing a payout that should have been distributed pro-rata to the stakers who were present while the reward was queued, or spread out over time, not entirely handed to whoever happens to hold the stake at the flush instant.

No modifier, nonReentrant guard, or reward-index streaming mechanism (e.g., time-based `rewardRate`/`periodFinish`) protects against this: `rewardPerTokenStored` is a single instantaneous step function driven by `totalStaked()` at the moment `_provisionReward` executes, and the trigger (`donateRewards`) is open to anyone. This directly violates the intended invariant that a queued backlog should be released against the stake set that actually earned it over time.

Additionally, `_provisionReward` trusts the caller-supplied `_amountReward` for both `historicalRewards` and the `rewardPerTokenStored` increment rather than checking the actual balance delta received via `safeTransferFrom`. If `_rewardToken` charges a transfer fee, the contract's real balance increases by less than `_amountReward`, so `historicalRewards`/`rewardPerTokenStored` become permanently inflated relative to `IERC20(_rewardToken).balanceOf(address(this))` — compounding the issue by letting an attacker trigger a full backlog flush cheaply (even 1 wei net-of-fee) while under-funding the pool, risking later stakers being unable to fully claim.

### Impact Explanation
This is theft of unclaimed yield: rewards queued for and rightfully attributable to the pool's historical/long-term stakers are instead redirected to an attacker who times a stake+donate sequence to coincide with the backlog flush, extracting value they did not earn through staking duration. In the fee-on-transfer variant, the pool's reward accounting (`historicalRewards`) also decouples from actual token balance, potentially leaving genuine stakers unable to claim fully — a form of yield loss/freezing for other users. This matches Immunefi **High – Theft of unclaimed yield**.

### Likelihood Explanation
- `donateRewards` requires no privileged role — any EOA/contract can call it as long as the token is already registered (`isRewardToken[_rewardToken]`).
- The precondition (`totalStaked() == 0` at some point while rewards are queued, e.g., pool launch before first deposit, or a period after full withdrawal) is a normal, expected lifecycle state, not a contrived edge case.
- The attacker only needs enough capital to be a meaningfully large fraction of `totalStaked()` at the flush moment (which is trivial right after pool launch, since total stake is low or zero), and a minimal `_amountReward` (down to 1 wei, or 1 wei net-of-fee).
- The attack is repeatable any time a reward token backlog exists and stake temporarily shrinks or the pool is fresh.

### Recommendation
- Restrict `donateRewards` similarly to `queueNewRewards` (e.g., only allow permissionless donation to *add* directly to `rewardPerTokenStored`/`queuedRewards` without ever flushing pre-existing `queuedRewards`), or require that any donation stream rewards over time rather than instantaneously step `rewardPerTokenStored`.
- Do not fold arbitrary pre-existing `queuedRewards` into the reward-per-token calculation based on the `totalStaked()` at an attacker-chosen instant; instead, require queued rewards to be released by a trusted manager, or implement a Synthetix-style streaming rate over a fixed duration so no single block/transaction can capture a full backlog.
- In `_provisionReward`, compute the actually-received amount via balance-before/after around `safeTransferFrom` and use that for `historicalRewards`/`rewardPerTokenStored` updates instead of the caller-supplied `_amountReward`, to keep `historicalRewards` reconciled with `IERC20(_rewardToken).balanceOf(address(this))` for fee-on-transfer tokens.

### Proof of Concept
Foundry/Hardhat test plan:
1. Deploy `BaseRewardPool` with a `stakingToken`, `masterMagpie` mock, and a registered `rewardToken` (optionally a fee-on-transfer mock ERC20).
2. As the reward manager, call `queueNewRewards(amount, rewardToken)` while `totalStaked() == 0` (no one has staked yet) — confirm `rewards[rewardToken].queuedRewards == amount` and `rewardPerTokenStored` unchanged.
3. Attacker deposits a small amount of `stakingToken` via `MasterMagpie` so that `totalStaked()` becomes attacker's balance only.
4. Attacker calls `donateRewards(1, rewardToken)` immediately.
5. Assert: `rewardPerTokenStored` jumps by approximately `queuedRewards / attackerStake`, and `earned(attacker, rewardToken)` now reflects (near) the full backlog despite attacker having zero prior staking duration.
6. Compare against a legitimate long-term staker (victim) who staked earlier but withdrew right before the attacker's deposit — show the victim's `earned()` does not include this backlog even though it accrued while they were staked, confirming the misdirected yield.
7. (Optional fee-token variant) configure `rewardToken` to deduct a transfer fee; call `donateRewards`, and assert `rewards[rewardToken].historicalRewards` exceeds `IERC20(rewardToken).balanceOf(address(this))` after the call, demonstrating the balance/accounting divergence.

### Citations

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

**File:** rewards/BaseRewardPool.sol (L279-284)
```text
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
