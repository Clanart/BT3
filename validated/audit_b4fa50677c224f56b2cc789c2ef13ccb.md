### Title
First-depositor theft of queued rewards when `DelegateVoteRewardPool.totalSupply` returns to zero - ([File: rewards/DelegateVoteRewardPool.sol])

### Summary
`DelegateVoteRewardPool` inherits the standard "queued rewards" pattern from `BribeRewardPool`/`BaseRewardPoolV2`, where rewards arriving while `totalSupply == 0` are parked in `queuedRewards` instead of being distributed. Because ordinary vlMGP holders can freely `vote`/`unvote` into the `delegatedPool` exactly like any other pool, `totalSupply` can organically (or via an attacker's own prior withdrawal) drop to zero, after which any unprivileged re-staker who is first to re-enter captures the entire queued reward balance regardless of when it accrued.

### Finding Description
`WombatBribeManager.vote()`/`unvote()` are public and treat `delegatedPool` the same as any other pool: any vlMGP locker can call `vote([delegatedPool], [delta])`, which invokes `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta)` on `DelegateVoteRewardPool`, satisfying the `onlyOperator` check because the call originates from `WombatBribeManager` itself [1](#0-0) . Likewise `unvote()` fully withdraws a user's balance [2](#0-1) .

If all current delegators unvote, `DelegateVoteRewardPool.totalSupply` becomes 0 (`_balances` cleared) [3](#0-2) . `harvestAll()` is a fully public, unrestricted external function that anyone can call at any time (including while `totalSupply == 0`), which pulls bribes via `claimAllBribes` and routes them into `_manageRewards` → `_queueNewRewardsWithoutTransfer` [4](#0-3) .

In `_queueNewRewardsWithoutTransfer`, when `totalSupply == 0` the incoming reward amount is stashed in `rewardInfo.queuedRewards` without updating `rewardPerTokenStored` [5](#0-4) . The next time this function runs with `totalSupply > 0`, the *entire* accumulated `queuedRewards` plus the new reward is folded into `rewardPerTokenStored`, dividing by the *current* (post-re-entry) `totalSupply` — with no accounting for who held balance during the zero-supply accrual window.

An unprivileged attacker (any vlMGP locker, even with 1 wei of votable weight) can therefore:
1. Wait until (or ensure, if they are the last remaining voter) `delegatedPool`'s `totalSupply` hits 0 via `unvote`.
2. Let bribe rewards accumulate into `queuedRewards` while `totalSupply == 0` (via `harvestAll()`, callable by anyone, or via `castVotes()` which calls `harvestAll()` automatically at line 293 of `wombat/WombatBribeManager.sol`).
3. Call `vote([delegatedPool], [1])` to become the sole depositor with `_balances[attacker] = 1`. The `updateRewards` modifier snapshots `userShare = balanceOf(attacker)` *before* the stake, which is 0, so this step accrues nothing to the attacker yet, and does not flush the queue itself (only `harvestAll` does).
4. Call `harvestAll()` again — this hits the `else` branch of `_queueNewRewardsWithoutTransfer` since `totalSupply == 1`, folding all of `queuedRewards` into `rewardPerTokenStored` divided by `totalSupply == 1`.
5. Call `getReward(attacker)` (via `WombatBribeManager.claimAllBribes`) to withdraw the fully inflated reward, none of which was earned by the attacker's actual (near-zero, near-instant) stake.

No existing modifier (`onlyOperator`, `updateRewards`) prevents this: `onlyOperator` only restricts the caller to be `WombatBribeManager`, which any user can trigger via `vote`; `updateRewards` correctly computes user-share *based on the corrupted `rewardPerTokenStored`*, which is the actual bug. There's no `nonReentrant` issue here — this is a state-accounting bug, not a reentrancy bug.

### Impact Explanation
This is theft of unclaimed yield: bribe rewards that should have been earned pro-rata by whichever accounts were staked in `delegatedPool` during the accrual window are instead captured entirely by whoever re-stakes first with a trivial amount after `totalSupply` returns to zero. This matches the Immunefi "theft of unclaimed yield" / "protocol insolvency for a subset of users" impact class — the diverted rewards are unrecoverable by the users who were entitled to them.

### Likelihood Explanation
- No privileged role is required; only vlMGP locking (which any user can do with tokens they hold) and vote/unvote calls, both public.
- The precondition (`totalSupply == 0` for `delegatedPool` momentarily) is achievable either organically (all delegators unvote, e.g., due to low APR or migrating votes) or attacker-induced if the attacker is (or becomes) the last/only voter, then withdraws, waits for a `castVotes()`/`harvestAll()` cycle to queue rewards, and re-enters with minimal stake.
- `harvestAll()` being callable by anyone at any time (not gated to only run inside `castVotes()`) makes timing highly controllable by the attacker: they can force the "queue" flush and the "distribute" flush to happen exactly when they choose.
- Repeatable every time `totalSupply` cycles through zero.

### Recommendation
Do not let a single new depositor claim rewards accrued during a period when they held no stake. Options:
- Route rewards accrued while `totalSupply == 0` to a fee/treasury address, or refuse to queue/distribute them until there is a fair basis for distribution (e.g., return them to bribe payers or burn/hold until averaged over a vesting period).
- Track `lastRewardTime` and require a minimum elapsed staking duration/weight-time before newly staked balances become eligible for previously queued rewards.
- Alternatively, snapshot `rewardPerTokenStored` update at the exact moment `totalSupply` transitions from 0 to non-zero using a very small "virtual" floor supply / precision-safe accounting so that a 1-wei stake cannot absorb a large `queuedRewards` balance disproportionately (e.g., require a minimum stake floor before flushing queued rewards, or flush queued rewards evenly across a lockup/vesting window rather than instantaneously into `rewardPerTokenStored`).

### Proof of Concept
Foundry test outline (`DelegateVoteRewardPoolZeroSupplyTheft.t.sol`):
1. Deploy `WombatBribeManager`, `DelegateVoteRewardPool` (as `delegatedPool`), and a mock bribe/reward token; register `delegatedPool` via `addPool`.
2. Have `userA` lock vlMGP and call `vote([delegatedPool], [100e18])` — `totalSupply == 100e18`.
3. Simulate reward accrual: have the bribe manager forward `1000e18` reward tokens through `harvestAll()` while `totalSupply == 100e18` is nonzero to establish a baseline (optional sanity check that `rewardPerTokenStored` updates normally).
4. Have `userA` call `unvote(delegatedPool)` — `totalSupply` becomes 0, `_balances[userA] = 0`.
5. Mock the bribe manager/voter to deliver a large reward payload (e.g., `5000e18`) and call `delegatedPool.harvestAll()` directly (or via `castVotes`) while `totalSupply == 0`; assert `rewards[token].queuedRewards == 5000e18` and `rewardPerTokenStored` unchanged.
6. `attacker` (separate address holding minimal locked vlMGP) calls `vote([delegatedPool], [1])` — `totalSupply == 1`.
7. Call `delegatedPool.harvestAll()` again with zero new bribes (or a trivial amount) — assert `rewardPerTokenStored` jumps by `5000e18 * 1e(decimals) / 1`.
8. `attacker` calls `getReward(attacker)` (or `claimAllBribes(attacker)`) and assert `earnedRewards == ~5000e18`, i.e., the attacker with 1 wei of stake for a few blocks receives the full reward that accrued while they held zero balance, while `userA` (who held 100e18 for the entire prior period) receives nothing from this batch.

### Citations

**File:** wombat/WombatBribeManager.sol (L182-220)
```text
    function vote(address[] calldata _lps, int256[] calldata _deltas) override public {
        if (_lps.length != _deltas.length)
            revert LengthMismatch();

        uint256 length = _lps.length;
        int256 totalUserVote;

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[_lps[i]];
            if (!pool.isActive)
                revert PoolNotActive();
            int256 delta = _deltas[i];
            totalUserVote += delta;
            if (delta != 0) {
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
                } else {
                    pool.totalVoteInVlmgp -= uint256(-delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] -= uint256(-delta);
                    IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(-delta), false);
                }
            }
        }

        if (msg.sender != delegatedPool) {
            if (totalUserVote > 0) {
                userTotalVotedInVlmgp[msg.sender] += uint256(totalUserVote);
                totalVlMgpInVote += uint256(totalUserVote);
            } else {
                userTotalVotedInVlmgp[msg.sender] -= uint256(-totalUserVote);
                totalVlMgpInVote -= uint256(-totalUserVote);
            }
        }

        if (userTotalVotedInVlmgp[msg.sender] > getUserVotable(msg.sender))
            revert NotEnoughVote();
    }
```

**File:** wombat/WombatBribeManager.sol (L223-237)
```text
    function unvote(address _lp) public {
        Pool storage pool = poolInfos[_lp];
        uint256 currentVote = userVotedForPoolInVlmgp[msg.sender][pool.poolAddress];
        if(!pool.isActive)
            revert PoolNotActive();
        
        pool.totalVoteInVlmgp -= uint256(currentVote);
        userTotalVotedInVlmgp[msg.sender] -= uint256(currentVote);
        userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] = 0;
        if (msg.sender != delegatedPool) {
            totalVlMgpInVote -= currentVote;
        }
        
        IBribeRewardPool(pool.rewarder).withdrawFor(msg.sender, uint256(currentVote), true);
    }
```

**File:** rewards/BribeRewardPool.sol (L72-85)
```text
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

**File:** rewards/DelegateVoteRewardPool.sol (L97-103)
```text
    function harvestAll() external {
        (
            address[] memory rewardTokensList,
            uint256[] memory earnedRewards
        ) = IWombatBribeManager(operator).claimAllBribes(address(this));
        _manageRewards(rewardTokensList, earnedRewards);
    }
```

**File:** rewards/DelegateVoteRewardPool.sol (L178-203)
```text
    function _queueNewRewardsWithoutTransfer(
        uint256 _amountReward,
        address _rewardToken
    ) internal {
        if (!isRewardToken[_rewardToken]) {
            rewardTokens.push(_rewardToken);
            isRewardToken[_rewardToken] = true;
        }
        Reward storage rewardInfo = rewards[_rewardToken];
        rewardInfo.historicalRewards =
            rewardInfo.historicalRewards +
            _amountReward;
        if (totalSupply == 0) {
            rewardInfo.queuedRewards += _amountReward;
        } else {
            if (rewardInfo.queuedRewards > 0) {
                _amountReward += rewardInfo.queuedRewards;
                rewardInfo.queuedRewards = 0;
            }
            rewardInfo.rewardPerTokenStored =
                rewardInfo.rewardPerTokenStored +
                (_amountReward * 10 ** this.stakingDecimals()) /
                totalSupply;
        }
        emit RewardAdded(_amountReward, _rewardToken);
    }
```
