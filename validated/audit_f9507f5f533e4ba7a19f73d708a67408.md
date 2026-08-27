### Title
Zero-`totalSupply` reward sniping in `DelegateVoteRewardPool._queueNewRewardsWithoutTransfer` allows a late staker to steal an entire harvested bribe distribution - ([File: rewards/DelegateVoteRewardPool.sol])

### Summary
When `totalSupply == 0` at the moment `harvestAll()` runs, `_queueNewRewardsWithoutTransfer` stores the harvested bribe amount in `rewardInfo.queuedRewards` instead of updating `rewardPerTokenStored`. The entire queued amount is only applied to `rewardPerTokenStored` on the *next* call to `_queueNewRewardsWithoutTransfer`, using whatever `totalSupply` exists at that later point in time - not the totalSupply/stakers that actually generated the bribes. An attacker who becomes the sole (or dominant) staker at that moment captures the whole queued reward.

### Finding Description
`stakeFor`/`withdrawFor` on `DelegateVoteRewardPool` are `onlyOperator` [1](#0-0)  but are reachable by any regular user through `WombatBribeManager.vote()`, which lets `msg.sender` stake/withdraw their own delegated vlMGP amount into the `delegatedPool` rewarder by supplying positive/negative deltas [2](#0-1) . `castVotes()` is fully permissionless and, when a `delegatedPool` is configured, triggers `IDelegateVoteRewardPool(delegatedPool).harvestAll()` at the end of its execution [3](#0-2) . `harvestAll()` itself is also externally callable without restriction [4](#0-3) .

The root cause is in `_queueNewRewardsWithoutTransfer`: [5](#0-4) 
When `totalSupply == 0`, the harvested amount is queued rather than distributed. On the subsequent harvest where `totalSupply > 0`, the *entire* queued amount (potentially accumulated from bribes earned while totalSupply was large, over an arbitrary historical period) is folded into `rewardPerTokenStored` based on the totalSupply present at that single point in time. Since `rewardPerTokenStored` determines each staker's `earned()` share going forward from their `userRewardPerTokenPaid` checkpoint (standard StakingRewards-style accounting inherited from `BribeRewardPool`), a staker who deposits a tiny amount right before this second harvest captures the full share of the queued rewards, since it's the only staked balance (or an outsized fraction of it) at the exact instant the reward-per-token update occurs.

None of the existing guards prevent this: `updateRewards` modifiers only checkpoint accounting correctly for existing balances, they do not protect against manipulated `totalSupply` timing; there is no minimum-stake-duration, no timelock on harvests, and no reentrancy issue is required — the exploit is purely about transaction/call ordering across `vote()`/`castVotes()`/`harvestAll()`/`getReward()`.

### Impact Explanation
This is a direct theft of unclaimed/historical bribe yield belonging to prior delegators. An attacker who can arrange for `totalSupply` to hit zero right before a harvest (e.g., by front-running/bundling around the last remaining delegator's withdrawal, then immediately triggering `castVotes()`/`harvestAll()`, then staking a dust amount) can redirect an entire epoch's harvested bribe reward to themselves instead of it being distributed proportionally to the users whose votes actually earned it. This matches the Immunefi impact class "theft of unclaimed yield" / "direct theft of user funds."

### Likelihood Explanation
Exploitation requires: (1) `totalSupply` of the `DelegateVoteRewardPool` to genuinely reach zero (i.e., all current delegators withdraw their delegated vlMGP in/around the same block — either coincidentally in a low-usage delegate pool, or via the attacker colluding with/front-running the last withdrawer's public mempool transaction), and (2) the attacker to control transaction ordering (bundle) so their `stakeFor`-triggering `vote()` call lands strictly after the harvest and before any other staker re-enters. This is realistic MEV-style behavior requiring no special privileges — only mempool visibility and standard bundling/front-running capability, which is well within the defined "unprivileged attacker" threat model. It is more likely to succeed in a delegate pool with few active delegators (higher chance of totalSupply transiently reaching zero) and is fully repeatable each time such a window occurs.

### Recommendation
Do not let a single staker capture an entire queued reward. Options: (a) instead of applying the full queued amount to `rewardPerTokenStored` based on totalSupply at the time of the next stake, distribute queued rewards ratably over time (streaming) similar to Synthetix/Convex `notifyRewardAmount` reward-rate patterns; (b) disallow/atomically batch `withdraw` such that `totalSupply` cannot go to zero while queued rewards are pending, or snapshot eligible stakers who held balance during the accrual period; (c) require a minimum bonding/cooldown period before newly staked balance becomes reward-eligible for pre-existing queued rewards.

### Proof of Concept
Foundry test plan:
1. Deploy `DelegateVoteRewardPool` (or use existing test harness) with `rewardToken`, `operator` = mock `WombatBribeManager`.
2. Have two delegators, `Alice` and `Bob`, `stakeFor` equal amounts (e.g., 100e18 each) via mocked operator calls, so `totalSupply = 200e18`.
3. Have `Alice` and `Bob` both `withdrawFor` their full balance in the same block (simulate atomic bundle), bringing `totalSupply` to 0.
4. Call `harvestAll()` (mock `claimAllBribes` to return a known reward amount, e.g. 1000e18 of `rewardToken`) — assert `rewardInfo.queuedRewards == 1000e18` and `rewardInfo.rewardPerTokenStored` unchanged.
5. Immediately (`stakeFor`) an attacker with 1 wei of stake, making `totalSupply = 1`.
6. Trigger a second `harvestAll()` with a token amount of 0 (or wait for next real harvest) to force `_queueNewRewardsWithoutTransfer` to fold `queuedRewards` into `rewardPerTokenStored` using `totalSupply = 1`.
7. Call `getReward(attacker)` and assert the attacker receives (approximately) the full 1000e18 reward, despite having contributed 0 economic weight/duration to earning it — proving reward misattribution and fund diversion away from Alice/Bob who generated it.

### Citations

**File:** rewards/DelegateVoteRewardPool.sol (L57-82)
```text
    function stakeFor(
        address _for,
        uint256 _amount
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply + _amount;
        _balances[_for] = _balances[_for] + _amount;
        _updateVote();

        emit Staked(_for, _amount);
    }

    function withdrawFor(
        address _for,
        uint256 _amount,
        bool _claim
    ) external override onlyOperator updateRewards(_for, rewardTokens) {
        totalSupply = totalSupply - _amount;
        _balances[_for] = _balances[_for] - _amount;
        _updateVote();

        emit Withdrawn(_for, _amount);

        if (_claim) {
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

**File:** rewards/DelegateVoteRewardPool.sol (L186-201)
```text
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
```

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

**File:** wombat/WombatBribeManager.sol (L291-294)
```text

        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();

```
