### Title
Flash-vote reward sniping via `voteAndCast` + immediate `unvote` allows theft of freshly harvested bribes - ([File: wombat/WombatBribeManager.sol])

### Summary
`voteAndCast` first calls `vote()`, which stakes the caller's delta into `BribeRewardPool` via `stakeFor` (increasing `totalSupply`), and then calls `castVotes()`, which triggers `wombatStaking.vote()` to harvest bribes and forward them into the same rewarder via `queueNewRewards`/`_provisionReward`. Because `_provisionReward` distributes the newly harvested reward using the **current** `totalStaked()` (which already includes the attacker's just-added delta), and because `BaseRewardPoolV2`'s `updateRewards` accounting has no vesting/cooldown, the attacker can immediately call `unvote()` (even in the very same transaction) to become entitled to a pro-rata share of the bribe that was just harvested, despite contributing zero real voting duration.

### Finding Description
The exploit chain is:

1. `vote()` calls `IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta))` [1](#0-0)  which runs `stakeFor`'s `updateRewards` modifier — this snapshots `userRewardPerTokenPaid = rewardPerTokenStored` **before** the harvest happens, then increases `totalSupply` and the user's `_balances` [2](#0-1) .
2. `castVotes()` (called right after inside `voteAndCast`) invokes `wombatStaking.vote(...)`, harvests bribes, and forwards them via `_forwardRewards` [3](#0-2) , which ultimately calls `_provisionReward`, updating `rewardPerTokenStored` using `totalStaked()` measured **after** the attacker's stake was already added [4](#0-3) .
3. The attacker (in the same transaction, or trivially in a subsequent block) calls `unvote(lp)`, which invokes `withdrawFor`, whose `updateRewards` modifier computes `_earned` using the now-updated `rewardPerTokenStored` versus the attacker's snapshot taken at step 1 [5](#0-4) [6](#0-5) . This credits the attacker with `delta * (R1 - R0) / 1e18`, i.e., a full pro-rata share of the bribe reward that was harvested entirely within the same block/transaction as their stake.

Nothing in `vote()`, `unvote()`, `stakeFor`, `withdrawFor`, or the `updateRewards` modifier enforces a minimum holding period, cooldown, or "reward must accrue over time held" restriction — the reward-per-token model treats any staked balance present at the moment `_provisionReward` runs as fully eligible for that entire distribution, regardless of how briefly it was held. There is no reentrancy guard or same-block restriction preventing an attacker's own contract from calling `voteAndCast` followed immediately by `unvote` (and `claimBribe`) atomically in one transaction.

### Impact Explanation
This is theft of unclaimed yield: real long-term voters who held their vlMGP vote positions throughout the actual bribe-accrual period have their proportional share of the harvested bribe diluted by an attacker who staked and unstaked within a single transaction/block, capturing reward proportional to `delta` with zero real economic exposure or time commitment. Every `voteAndCast` call (called by anyone, including the attacker themselves) is an opportunity to skim value away from genuine voters.

### Likelihood Explanation
- No privileged role is required; the attacker only needs unused vlMGP voting capacity (`getUserVotable`), which is normal for any vlMGP holder.
- The attack can be executed by a simple attacker-deployed contract calling `voteAndCast` then `unvote` (and `claimBribe`) sequentially in one atomic transaction — no bundle or cross-block timing is even strictly necessary.
- It is repeatable every time bribes are pending to be harvested (i.e., whenever `castVotes`/`voteAndCast` would produce a nonzero harvest), making it a recurring drain rather than a one-off.

### Recommendation
Decouple reward eligibility from instantaneous stake size at harvest time. Options: (1) apply a minimum holding/cooldown period before newly staked vote deltas participate in reward distribution (e.g., snapshot eligible balances prior to the block/harvest rather than including same-block deposits); (2) require `vote()`/`stakeFor` deltas to be excluded from `totalStaked()` used in `_provisionReward` until a subsequent harvest cycle; (3) charge a deposit/withdrawal fee on vote deltas that are unvoted within the same "epoch" as they were cast, offsetting the sniped reward.

### Proof of Concept
Hardhat test outline:
1. Deploy `WombatBribeManager`, `BribeRewardPool`, and mock `wombatStaking`/`voter` such that `wombatStaking.vote()` returns a nonzero bribe amount for an active pool `lp`.
2. Fund attacker with vlMGP lock via mock `IVLMGP` so `getUserVotable(attacker) >= delta`.
3. Have a legitimate voter call `vote([lp],[+longTermDelta])` beforehand and let one harvest cycle pass without unvoting, establishing a baseline "real voter" share.
4. From attacker's own contract, in a single transaction: call `voteAndCast([lp],[+delta],false)` (triggering the mocked bribe harvest of amount `B`), then immediately call `unvote(lp)`, then `claimBribe([lp])`.
5. Assert: attacker's claimed reward ≈ `B * delta / (delta + longTermDelta)`, i.e., a nonzero share of `B` proportional only to `delta`, even though `blocksHeld == 0` for the attacker versus `longTermDelta`'s full accrual period — demonstrating reward capture disproportionate to `voteAmount * blocksHeld`.

### Citations

**File:** wombat/WombatBribeManager.sol (L196-199)
```text
                if (delta > 0) {
                    pool.totalVoteInVlmgp += uint256(delta);
                    userVotedForPoolInVlmgp[msg.sender][pool.poolAddress] += uint256(delta);
                    IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, uint256(delta));
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

**File:** wombat/WombatBribeManager.sol (L271-296)
```text
        (address[][] memory rewardTokens, uint256[][] memory feeAmounts) = wombatStaking.vote(
            _pools,
            votes,
            rewarders,
            msg.sender
        );

        // comment outs for now since chainlink fails sometimes
        // if (swapForBnb) {
        //     finalFeeAmounts = new uint256[][](1);
        //     finalFeeAmounts[0] = new uint256[](1);
        //     finalFeeAmounts[0][0] = _swapFeesForBnb(rewardTokens, feeAmounts);
        //     finalRewardTokens = new address[][](1);
        //     finalRewardTokens[0] = new address[](1);
        //     finalRewardTokens[0][0] = address(0);
        // } else {
            _forwardRewards(rewardTokens, feeAmounts);
            finalRewardTokens = rewardTokens;
            finalFeeAmounts = feeAmounts;
        // }

        // send rewards to the delegate pool
        if (delegatedPool != address(0)) IDelegateVoteRewardPool(delegatedPool).harvestAll();

        emit VoteCasted(msg.sender, lastCastTime);
    }
```

**File:** rewards/BribeRewardPool.sol (L57-67)
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
