Confirmed analog: `BaseRewardPoolV2._provisionReward` distributes newly harvested bribe rewards instantly to whoever holds a share at that block, rather than streaming them over time [1](#0-0) . `WombatBribeManager.vote` lets any wallet call `stakeFor` on the pool's `BribeRewardPool` at any time before `castVotes` is invoked [2](#0-1) , and `castVotes` triggers `WombatStaking.vote` which calls `queueNewRewards` on the same rewarder with the freshly harvested bribe amount [3](#0-2) .

### Title
Front-runnable bribe reward sniping via instant `vote()`-then-`castVotes()` sequencing - (File: wombat/WombatBribeManager.sol, rewards/BaseRewardPoolV2.sol)

### Summary
`WombatBribeManager.vote()` allows an unprivileged wallet to increase its `vlMGP` vote weight for a pool at any moment, which immediately calls `BribeRewardPool.stakeFor` and updates the user's share of that pool's bribe rewarder [4](#0-3) . There is no delay, epoch boundary, or cooldown between voting and reward eligibility. Anyone can monitor the mempool/state for an imminent `castVotes()` call (which harvests bribes from Wombat and instantly queues them into `rewardPerTokenStored` for that pool's rewarder proportional to current `totalStaked()`), submit a `vote()` transaction with a large delta just before it, and then immediately `unvote()`/withdraw and `claimBribe()` right after, capturing a full share of bribes that were actually earned by long-term voters over the preceding period.

### Finding Description
`BaseRewardPoolV2._provisionReward` (invoked via `queueNewRewards`) increases `rewardPerTokenStored` instantaneously based on the pool's `totalStaked()` at the moment the reward arrives [5](#0-4) . There is no time-weighted vesting/streaming of rewards (unlike a typical `periodFinish`/`rewardRate` streaming design seen elsewhere in the codebase, e.g. `wombat/WomUp.sol` [6](#0-5) ). Because `stakeFor`/`withdrawFor` on `BribeRewardPool` can be called by any voter at any time via `WombatBribeManager.vote`/`unvote` with no lock-up, cooldown, or minimum holding period tied to the voting/bribe-accrual period, a wallet can:
1. Detect (or self-trigger) an imminent `castVotes()` call that will harvest a large bribe batch for a target pool [7](#0-6) .
2. Call `vote()` with a large positive delta for that pool immediately beforehand, instantly registering a large `stakeFor` balance in `BribeRewardPool` [4](#0-3) .
3. Let `castVotes()` execute, which calls `wombatStaking.vote` → `queueNewRewards`, instantly crediting `rewardPerTokenStored` proportional to the attacker's now-inflated share of `totalSupply` [8](#0-7) .
4. Call `unvote()`/`vote()` with a negative delta and `claimBribe()` to withdraw the harvested bribe and exit, having held the position for effectively zero time relative to the accrual period.

This mirrors the reported bug class: the attacker "joins" (adds vote/stake weight) only after the equivalent of the "question reveal" (the bribe harvest event is imminent/known), capturing rewards proportional to work/commitment they never actually contributed, at the expense of the honest voters who held their position throughout the full bribe-accrual period.

### Impact Explanation
This directly enables theft of unclaimed bribe yield from legitimate long-term voters: the sniping wallet extracts a disproportionate share of harvested bribes funded by real voters' sustained vote commitment, diluting/reducing the rewards those voters ultimately receive. This qualifies as theft/permanent loss of unclaimed yield for other users, satisfying the required impact bar.

### Likelihood Explanation
Likelihood is high: `vote()`, `unvote()`, and `castVotes()` are all unprivileged, permissionless, publicly callable functions [9](#0-8) ; `castVotes()` itself is externally callable by anyone (with a caller fee incentive), so an attacker can even self-trigger the harvest immediately after front-running their own vote in the same or adjacent transaction/block, requiring no privileged access or unusual conditions.

### Recommendation
Introduce a minimum holding/lock period (or snapshot-based accounting) before a voter's stake becomes eligible for bribes harvested via `castVotes()`, e.g., only count vote weight that was present as of the last `castVotes()` snapshot when queuing new rewards, or require votes to be locked for a minimum duration (analogous to disallowing "join" after the reward-triggering event has already been revealed/queued) before they can accrue newly harvested bribes.

### Proof of Concept
1. Attacker observes `WombatBribeManager.lastCastTime`/pending bribe state indicating a large `castVotes()` harvest is imminent (or attacker calls `castVotes()` themselves).
2. In the same or preceding transaction, attacker calls `vote(lps, deltas)` with a large positive delta for the target pool, instantly increasing their `BribeRewardPool` balance via `stakeFor` [4](#0-3) .
3. Attacker calls `castVotes(false)`, which harvests bribes and calls `queueNewRewards` on the target pool's rewarder, instantly crediting `rewardPerTokenStored` based on current `totalStaked()` (now inflated by attacker's snipe) [10](#0-9) .
4. Attacker calls `unvote()`/negative `vote()` delta and `claimBribe()` to withdraw their share of the harvested bribe and exit the position immediately [11](#0-10) .

### Citations

**File:** rewards/BaseRewardPoolV2.sol (L273-313)
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

    /* ============ Internal Functions ============ */

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

**File:** wombat/WombatBribeManager.sol (L182-237)
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

    /// @notice Unvote from an inactive pool. This makes it so that deleting a pool, or changing a rewarder doesn't block users from withdrawing
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

**File:** wombat/WombatBribeManager.sol (L241-296)
```text
    function castVotes(bool swapForBnb)
        override public
        returns (address[][] memory finalRewardTokens, uint256[][] memory finalFeeAmounts)
    {
        lastCastTime = block.timestamp;
        uint256 length = pools.length;
        address[] memory _pools = new address[](length);
        int256[] memory votes = new int256[](length);
        address[] memory rewarders = new address[](length);

        for (uint256 i; i < length; i++) {
            Pool storage pool = poolInfos[pools[i]];
            _pools[i] = pool.poolAddress;
            rewarders[i] = pool.rewarder;

            uint256 currentVote = getVoteForLp(pool.poolAddress);
            uint256 targetVoteInLMGP = pool.totalVoteInVlmgp;
            uint256 targetVote = 0;

            if (totalVlMgpInVote != 0) {
                targetVote = targetVoteInLMGP * totalVotes() / totalVlMgpInVote;
            }

            if (targetVote >= currentVote) {
                votes[i] = int256(targetVote - currentVote);
            } else {
                votes[i] = int256(targetVote) - int256(currentVote);
            }
        }

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

**File:** wombat/WombatStaking.sol (L374-411)
```text
        uint256[][] memory rewardAmounts = voter.vote(_lpVote, _deltas);
        rewardTokens = new IERC20[][](rewardAmounts.length);
        callerFeeAmounts = new uint256[][](rewardAmounts.length);

        for (uint256 i; i < rewardAmounts.length; i++) {

            address bribesContract = address(voter.infos(_lpVote[i]).bribe);

            if (bribesContract != address(0)) {
                rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens();
                callerFeeAmounts[i] = new uint256[](rewardAmounts[i].length);

                for (uint256 j; j < rewardAmounts[i].length; j++) {
                    uint256 rewardAmount = rewardAmounts[i][j];
                    uint256 callerFeeAmount = 0;

                    if (rewardAmount > 0) {
                        // if reward token is bnb, wrap it first
                        if (address(rewardTokens[i][j]) == address(0)) {
                            Address.sendValue(payable(wbnb), rewardAmount);
                            rewardTokens[i][j] = IERC20(wbnb);
                        }

                        uint256 protocolFee = (rewardAmount * bribeProtocolFee) / DENOMINATOR;

                        if (protocolFee > 0) {
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeFeeCollector, protocolFee);
                        }

                        if (caller != address(0) && bribeCallerFee != 0) {
                            callerFeeAmount = (rewardAmount * bribeCallerFee) / DENOMINATOR;
                            IERC20(rewardTokens[i][j]).safeTransfer(bribeManager, callerFeeAmount);
                        }

                        rewardAmount -= protocolFee;
                        rewardAmount -= callerFeeAmount;
                        IERC20(rewardTokens[i][j]).safeApprove(_rewarders[i], rewardAmount);
                        IBaseRewardPool(_rewarders[i]).queueNewRewards(rewardAmount, address(rewardTokens[i][j]));
```

**File:** wombat/WomUp.sol (L100-108)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalSupply() == 0) {
            return rewardPerTokenStored;
        }
        return
            rewardPerTokenStored + (
                (lastTimeRewardApplicable() - (lastUpdateTime)) * rewardRate * (1e18) / (totalSupply())
            );
    }
```
