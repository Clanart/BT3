Confirmed: `_provisionReward` (called by `queueNewRewards`) applies rewards as an **instant lump-sum increment** to `rewardPerTokenStored`, proportional to `totalStaked()` at the moment of the call [1](#0-0) , rather than streaming them over time (no `rewardRate`/`periodFinish` vesting mechanism exists in this pool). Combined with `updateRewards`, which snapshots `userRewardPerTokenPaid` on every `stakeFor`/`withdrawFor` call [2](#0-1) , and `WombatBribeManager.vote`, which lets a user freely reallocate already-locked vlMGP into and out of a pool with no cooldown [3](#0-2) , a user can transiently stake right before a `queueNewRewards` call and withdraw right after, capturing a proportional share of the newly queued rewards for zero real holding duration.

### Title
Instant-vote sandwiching of bribe reward distribution allows theft of yield from long-term voters - (File: rewards/BaseRewardPoolV2.sol)

### Summary
`BribeRewardPool` (via `BaseRewardPoolV2`) distributes newly queued rewards as an instantaneous lump-sum addition to `rewardPerTokenStored`, weighted only by `totalStaked()` at the exact block of the `queueNewRewards` call, with no time-based vesting. Because `WombatBribeManager.vote()` allows any vlMGP holder to freely increase and decrease their vote allocation to a specific pool with no cooldown, an attacker can insert a `stakeFor` immediately before and a `withdrawFor` immediately after a `queueNewRewards` call, capturing a share of rewards proportional to their transient stake, diluting the share due to durable voters.

### Finding Description
The reward accounting works as follows:
- `_provisionReward` increases `rewardPerTokenStored` by `(_amountReward * 10**decimals) / totalStaked()` at the instant it is called [4](#0-3) .
- `stakeFor`/`withdrawFor` in `BribeRewardPool` both apply `updateRewards(_for, rewardTokens)`, which computes `_earned` using the account's balance and the delta between current and stored `rewardPerToken`, then updates the checkpoint [5](#0-4) [2](#0-1) .
- `WombatBribeManager.vote()` is a plain public function with no per-block/cooldown restriction: any user can call `vote(_lps, [+X])` then `vote(_lps, [-X])` in the same block (or even same transaction, e.g., through `voteAndCast`), each time invoking `stakeFor`/`withdrawFor` on the corresponding `BribeRewardPool` [3](#0-2) .
- `castVotes()`, which triggers `wombatStaking.vote()` and ultimately `IBaseRewardPool(_rewarders[i]).queueNewRewards(...)`, is itself a public/permissionless function [6](#0-5) [7](#0-6) , so an attacker (or anyone) can trigger the harvest/queue step deterministically inside the same transaction sandwich.

Exploit sequence, all attacker-controlled and in a single transaction (via `voteAndCast` or three chained calls):
1. `vote(_lps, [+X])` → `stakeFor(attacker, X)` on the pool's `BribeRewardPool`, checkpointing `userRewardPerTokenPaid` at the pre-harvest value.
2. `castVotes()`/`voteAndCast()` executes `wombatStaking.vote()`, harvesting real Wombat bribes and calling `queueNewRewards`, which bumps `rewardPerTokenStored` using `totalStaked()` that now includes the attacker's `X`.
3. `vote(_lps, [-X])` → `withdrawFor(attacker, X, false)`, which recomputes `_earned` using the new `rewardPerToken`, crediting the attacker with `X * Δ(rewardPerToken)` even though they held the position for zero real accrual time before the harvest.
4. `claimBribe`/`claimBribeFor` transfers the credited reward out.

No existing modifier prevents this: `updateRewards` only checkpoints reward per token, it does not enforce a minimum holding duration; there is no `nonReentrant`-style time-lock, no last-vote timestamp check per user, and `getUserVotable` only checks total vlMGP capacity, not duration. The attacker's real capital requirement is only genuinely locked vlMGP (already held, no flash-lock needed since `vote()` only reallocates existing lock weight, it does not create new locks) — but no minimum holding period is required for that lock to participate in a specific pool's bribe reward split.

### Impact Explanation
This is a real, quantifiable transfer of unclaimed yield away from voters who maintained their vote allocation for the entire accrual period, to a "flash voter" who only had exposure during the single block/tx where the harvest landed. This matches the Immunefi "theft of unclaimed yield" impact class: the attacker's share of the harvested bribe reward is `X / totalStaked()` of the full newly queued amount, extracted with zero opportunity cost or holding-time risk, at the expense of durable voters whose accrued share is diluted for that harvest event.

### Likelihood Explanation
- Requires the attacker to hold sufficient vlMGP (a genuine, pre-existing lock — capital cost, not flash-loanable due to lock mechanics), but no minimum holding duration on that lock is enforced for participating in a single pool's reward split.
- `castVotes()`/`voteAndCast()` are both public and permissionless, so the attacker fully controls when the harvest/queue step executes relative to their stake/unstake, making the timing deterministic and repeatable every time bribes accumulate (each vote epoch/harvest cycle).
- The attack is trivially repeatable and requires no privileged role, no oracle manipulation, and no reentrancy — only the public `vote`, `castVotes`/`voteAndCast`, and `claimBribe` functions.

### Recommendation
Change the bribe reward pool from a lump-sum `rewardPerTokenStored` bump model to a time-weighted/streamed distribution (e.g., Synthetix-style `rewardRate` over a fixed `rewardsDuration`), so newly queued rewards accrue gradually rather than being instantly claimable by whoever is staked at the exact harvest block. Alternatively, enforce a minimum holding period (e.g., require vote allocation to persist across at least one full epoch/`lastCastTime` cycle) before a user's stake is eligible for the reward delta from a given `queueNewRewards` call, or snapshot eligible balances prior to the harvest transaction rather than using the live `totalStaked()`/`balanceOf` at call time.

### Proof of Concept
Foundry test plan:
1. Deploy `WombatBribeManager`, `WombatStaking`, and a `BribeRewardPool` for a pool with an existing long-term voter (`voterA`) holding vote weight `Y` from a prior block.
2. Fund an attacker with vlMGP lock of size `X` (via normal `lock()`/masterMagpie staking, done in setup, not part of the timed attack).
3. In a single test transaction:
   - Call `bribeManager.vote([lp], [X])` as attacker → confirm `BribeRewardPool.balanceOf(attacker) == X` and `userRewardPerTokenPaid` checkpointed at pre-harvest value.
   - Call `bribeManager.castVotes(false)` (or have the harness directly call `wombatStaking.vote(...)` to simulate the Wombat bribe harvest) which results in `BribeRewardPool.queueNewRewards(rewardAmount, token)`, bumping `rewardPerTokenStored`.
   - Call `bribeManager.vote([lp], [-X])` as attacker → triggers `withdrawFor`, updating `userRewards[token][attacker]`.
   - Call `bribeManager.claimBribe([lp])` as attacker.
4. Assert `IERC20(token).balanceOf(attacker) > 0` despite the attacker's net vote duration for that pool being zero blocks, and assert this amount equals `rewardAmount * X / (X + Y)`, demonstrating dilution of `voterA`'s expected share and improper reward capture by a transient staker.

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

**File:** wombat/WombatBribeManager.sol (L315-322)
```text
    function voteAndCast(
        address[] calldata _lps,
        int256[] calldata _deltas,
        bool swapForBnb
    ) external returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts) {
        vote(_lps, _deltas);
        (finalRewardTokens, finalFeeAmounts) = castVotes(swapForBnb);
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

**File:** wombat/WombatStaking.sol (L403-411)
```text
                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
```
