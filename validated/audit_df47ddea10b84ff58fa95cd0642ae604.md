### Title
Unbounded revert in `castVotes` loop can permanently freeze bribe harvesting and vote casting for all pools - ([File: wombat/WombatBribeManager.sol])

### Summary
`WombatBribeManager.castVotes()` iterates over the entire `pools` array and submits all pools' vote deltas in a single external call to `WombatStaking.vote()`, which in turn calls `queueNewRewards()` on each pool's rewarder in a loop. If any single pool in that array causes `queueNewRewards` (or any other call inside the loop) to revert, the whole batch transaction reverts, blocking vote casting and bribe/reward harvesting for every pool and every voter — not just the pool with the problem. This mirrors the `BulkCreateUnstakes` bug class: a hard stop on one bad element of a batch, where the offending entry is never removed from the iterated state and will keep causing every future call to fail, resulting in a persistent DoS.

### Finding Description
`castVotes()` builds vote deltas for **all** registered pools and forwards them together to `wombatStaking.vote()`: [1](#0-0) 

`WombatStaking.vote()` then loops over every pool in the batch and, for any pool with a non-zero reward amount, calls `queueNewRewards()` on that pool's rewarder: [2](#0-1) 

`queueNewRewards` is gated by an `onlyManager` modifier on the rewarder contract: [3](#0-2) 

Because there is no per-pool isolation (no try/catch, no skip-and-continue), if a single pool's rewarder reverts `queueNewRewards` for any reason (e.g., the rewarder no longer authorizes `wombatStaking` as a manager after `updatePoolHelper` rotates the rewarder, or a reward token behaves unexpectedly during `safeTransfer`/`safeApprove` in the same loop), the entire `vote()` call reverts, and therefore the entire `castVotes()` transaction reverts. Since `pools` is never automatically pruned of a bad entry (only `removePool(uint256 _index)` by the owner can remove it), every subsequent call to `castVotes()` — the only function that both casts votes to Wombat and forwards/harvests bribes for all pools — will keep failing until an admin intervenes.

### Impact Explanation
`castVotes` is the sole mechanism to advance voting and pull/forward bribes from Wombat for every pool tracked by `WombatBribeManager`. A revert in a single pool's processing freezes bribe harvesting and vote-weight updates for **all** pools and **all** vlMGP voters simultaneously, not just the affected pool. Because bribes continue to accrue in the underlying Wombat bribe contracts but cannot be pulled/forwarded while `castVotes` reverts, and votes cannot be recast to reflect changing vlMGP allocations, this results in a freeze of unclaimed yield across the whole voting system that persists until governance manually removes the offending pool — easily exceeding a 24-hour freeze window, satisfying a permanent-freeze-of-unclaimed-yield impact.

### Likelihood Explanation
`castVotes()` is a public function callable by any address (it even pays a caller fee for gas compensation), so exploitation/triggering does not require privileged access — an ordinary user's normal use of the voting flow (or natural state drift from admin pool-config changes such as `updatePoolHelper`) can produce the failing condition. Once one pool enters a state where its rewarder call reverts, every future permissionless call to `castVotes` fails identically, so the condition is self-perpetuating and highly likely to be hit in a live protocol with multiple pools and periodic rewarder/config changes.

### Recommendation
Do not let a single pool's processing failure abort the entire batch:
- Wrap the per-pool reward-forwarding logic in `WombatStaking.vote()` (and/or the per-pool loop in `WombatBribeManager.castVotes()`) in a low-level call with try/catch, logging/skipping the failing pool instead of reverting the whole transaction.
- Alternatively, split `castVotes` into per-pool or chunked calls so that a failure in one pool cannot block processing of the others, similar to the suggested fix in the referenced report (log-and-continue rather than hard-revert).

### Proof of Concept
1. Owner registers pool A and pool B in `WombatBribeManager` via `addPool`, each pointing at a rewarder in `WombatStaking`/`MasterMagpie`.
2. Owner later calls `WombatStaking.updatePoolHelper` for pool A's underlying pool to rotate its rewarder, but the bribe manager's `poolInfos[A].rewarder` is not synchronized (or the new rewarder's `managers[wombatStaking]` flag is not re-enabled).
3. Bribes accumulate for pool A and pool B in Wombat's bribe contract.
4. Any user calls `WombatBribeManager.castVotes(false)`. Inside `WombatStaking.vote()`, when the loop reaches pool A, `IBaseRewardPool(_rewarders[i]).queueNewRewards(...)` reverts because `wombatStaking` is no longer an authorized `manager` on the (rotated) rewarder for pool A.
5. The revert bubbles up through `vote()` and `castVotes()`, reverting the whole transaction — pool B's votes are never cast and its bribes are never forwarded, even though pool B had no issue.
6. Every subsequent call to `castVotes()` by any user reverts the same way, since pool A remains in the `pools` array; bribes for both pools keep accruing unharvested until the owner calls `removePool` to evict pool A — a manual, indefinite-length freeze of yield for all pools/voters. [4](#0-3)

### Citations

**File:** wombat/WombatBribeManager.sol (L241-277)
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

```

**File:** wombat/WombatBribeManager.sol (L436-441)
```text
    function removePool(uint256 _index) external onlyOwner {
        uint256 length = pools.length;
        if(_index >= length) revert OutOfPoolIndex();
        pools[_index] = pools[length - 1];
        pools.pop();
    }
```

**File:** wombat/WombatStaking.sol (L378-412)
```text
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
                    }
```

**File:** rewards/BaseRewardPool.sol (L258-274)
```text
    /// @notice Sends new rewards to be distributed to the users staking. Only callable by manager
    /// @param _amountReward Amount of reward token to be distributed
    /// @param _rewardToken Address reward token
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
