# Q2177: mWOMSVBaseRewarder.updateFor - early-continue skips a real balance change

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Assuming the computed forfeit lands just above the _amount / 1000 dust threshold, can an unprivileged attacker turn this into a divergence between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` via `updateFor(address _account)`, breaking the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just above the _amount / 1000 dust threshold, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `rewards[_rewardToken].rewardPerTokenStored` relation are unchanged by the attacker's transaction.
