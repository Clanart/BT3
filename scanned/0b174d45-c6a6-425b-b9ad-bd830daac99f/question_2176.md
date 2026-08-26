# Q2176: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
rewards/vlMGPBaseRewarder.sol: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. With the victim address and the block at which their index is pinned under attacker control and the computed forfeit lands just above the _amount / 1000 dust threshold, can an unprivileged caller sequence `updateFor(address _account)` so that `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under the computed forfeit lands just above the _amount / 1000 dust threshold, asserting at the end that `userRewards[_rewardToken][account]` still equals `rewards[_rewardToken].rewardPerTokenStored` and the PoC's balance delta is non-positive.
