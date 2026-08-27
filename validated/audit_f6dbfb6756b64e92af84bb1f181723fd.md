### Title
Reward-pool "flash-vote" front-running of `castVotes()` allows theft of bribe yield from long-term voters via instantaneous `rewardPerTokenStored` lump-sum accounting - ([File: rewards/BaseRewardPoolV2.sol], [File: rewards/BribeRewardPool.sol], [File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.vote()`/`unvote()` call `stakeFor`/`withdrawFor` on the per-pool `BribeRewardPool`, which uses a lump-sum, non-time-weighted `rewardPerTokenStored` update model rather than a continuous per-second accrual model. Because `queueNewRewards`/`_provisionReward` (triggered inside `castVotes()`) divides the newly-harvested bribe by whatever `totalStaked()` happens to be at that exact instant, an attacker who inflates their vote weight for a pool immediately before `castVotes()` and reverses it immediately after captures a disproportionate share of the harvested reward for zero real staking duration.

### Finding Description
The reward accounting in [1](#0-0)  updates `rewardPerTokenStored` as a single lump-sum increment:
```
rewardInfo.rewardPerTokenStored += (_amountReward * 10**decimals) / totalStaked();
```
This is invoked once per harvest (from `queueNewRewards`, called by the reward manager after `WombatBribeManager.castVotes()` forwards harvested bribes) rather than being streamed continuously over time. The `DelegateVoteRewardPool` variant has the identical pattern at [2](#0-1) .

`vote()`/`unvote()` in `WombatBribeManager.sol` call `stakeFor`/`withdrawFor` on the rewarder with no cooldown, minimum-duration, or same-block restriction: [3](#0-2)  and [4](#0-3) . `stakeFor`/`withdrawFor` on `BribeRewardPool` immediately mutate `totalSupply`/`_balances` and checkpoint the caller's `userRewardPerTokenPaid` via the `updateRewards` modifier before the balance change is applied: [5](#0-4)  and [6](#0-5) .

Exploit flow:
1. Attacker observes a pending `castVotes(bool)` transaction in the mempool (or the fact that `lastCastTime` harvest is imminent).
2. Attacker (already holding vlMGP with spare votable capacity - `userTotalVotedInVlmgp[msg.sender] <= getUserVotable(msg.sender)`) calls `vote(_lps, _deltas)` with a large positive delta on the target pool, front-running `castVotes()`. This inflates `totalSupply`/`_balances[attacker]` on that pool's `BribeRewardPool` and sets the attacker's `userRewardPerTokenPaid` checkpoint to the pre-harvest value.
3. `castVotes()` executes, `wombatStaking.vote(...)` harvests bribes and (via the reward manager) calls `queueNewRewards` on the rewarder, which computes `rewardPerTokenStored += reward * decimals / totalStaked()` using the now-inflated `totalStaked()` that includes the attacker's flash stake.
4. Since `rewardPerToken` increases uniformly for every staked unit, and the attacker's balance is large relative to genuine long-term voters, the attacker earns a large absolute share of the newly minted `rewardPerToken` delta despite having staked for effectively zero blocks.
5. Attacker immediately calls `vote()` again with the negative delta (or `unvote()`) to withdraw, locking in the reward via `earned()`/`claimBribe()`.

No modifier, nonReentrant guard, or reward-index mechanism in `updateRewards`/`_provisionReward` prevents this, because the reward model has no concept of "time held" - it is a per-transaction distribution keyed purely on the instantaneous `totalStaked()` snapshot.

### Impact Explanation
This constitutes theft of unclaimed yield from genuine, long-term voters: bribe rewards that should have accrued proportionally to the pre-existing stake-time of real voters are instead diverted to an attacker whose stake existed only within the sandwiched transaction pair. This matches the "theft of unclaimed yield" Immunefi impact class for the `WombatBribeManager`/`BribeRewardPool`/`DelegateVoteRewardPool` reward-distribution scope.

### Likelihood Explanation
- No new capital is strictly required beyond already holding some vlMGP with unused votable capacity (or reallocating existing votes away from other pools temporarily), since `vote()` deltas are simply reallocations bounded by `getUserVotable(msg.sender)`.
- The attack is fully repeatable every time `castVotes()`/`harvestSinglePool()`/`voteAndCast()` is called, since these are the only triggers for the lump-sum `queueNewRewards` call.
- It requires ordinary mempool front-running/back-running capability (same-block or adjacent-block sandwich), which is standard MEV behavior available to any unprivileged actor and does not require flash loans, reentrancy, or privileged roles.
- The larger the attacker's reallocatable vlMGP stake relative to the pool's total vote weight, the larger the captured share, making this scale with attacker's existing legitimate holdings.

### Recommendation
Replace the lump-sum, instantaneous `rewardPerTokenStored` update with a time-weighted/streaming accrual model (e.g., Synthetix-style `rewardRate` distributed per second, with `lastUpdateTime` checkpoints), or enforce a minimum holding period / cooldown between `vote()` (stake) and `unvote()`/negative `vote()` (withdraw) on the same pool so that a stake cannot be added and removed within the same harvest cycle. Alternatively, snapshot voter balances prior to `castVotes()` execution and disallow vote changes on pools with a harvest already queued in the same block.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, a `BribeRewardPool` for one pool, and mock `wombatStaking`/`voter`/bribe contracts that return a fixed bribe reward amount when `wombatStaking.vote()` is called (simulating `castVotes()` harvesting bribes and forwarding them to `queueNewRewards`).
2. Set up `userA` with locked vlMGP, call `vote()` to allocate votes to the pool well before the test's harvest block, and let several blocks pass (simulating genuine long-term voting).
3. Give `attacker` a smaller amount of separately-idle votable vlMGP (or have attacker reallocate votes from an unrelated pool).
4. In the harvest block: attacker calls `vote(pool, +largeDelta)` (front-run), then the test calls `castVotes(false)` (simulating the harvest), then attacker immediately calls `vote(pool, -largeDelta)` (back-run) in the same block.
5. Assert: `attacker`'s `earned()`/`claimBribe()` result is > 0 and disproportionate to (attacker's stake-time / total stake-time), and `userA`'s `earned()` is measurably lower than the expected fair share `userA` would have received had the attacker not sandwiched the harvest (compute expected fair share as `userA.balance * totalReward / totalStakeTimeWeighted` and compare against actual `rewardPerTokenStored`-based `earned()`).
6. Confirm total rewards paid out (`attacker.earned + userA.earned`) still sums to the harvested reward amount, proving it is a reallocation/theft of yield between users rather than a supply-inflation bug.

### Citations

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

**File:** rewards/BaseRewardPoolV2.sol (L290-314)
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
