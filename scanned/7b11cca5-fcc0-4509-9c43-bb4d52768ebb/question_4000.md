# Q4000: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
rewards/vlMGPBaseRewarder.sol: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. With the victim address and the block at which their index is pinned under attacker control and the attacker locks one block before a known large settlement and unlocks one block after, can an unprivileged caller sequence `updateFor(address _account)` so that `rewards[_rewardToken].historicalRewards` and `IERC20(_rewardToken).balanceOf(address(this))` no longer reconcile, violating the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks one block before a known large settlement and unlocks one block after, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `rewards[_rewardToken].historicalRewards` versus `IERC20(_rewardToken).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
