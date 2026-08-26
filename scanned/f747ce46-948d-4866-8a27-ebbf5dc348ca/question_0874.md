# Q0874: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
In rewards/vlMGPBaseRewarder.sol, _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Starting from a state where the account's slot matured recently so the percent has only just begun to decay, can an unprivileged EOA use `updateFor(address _account)` to leave `rewards[_rewardToken].historicalRewards` inconsistent with `IERC20(_rewardToken).balanceOf(address(this))`, violating the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the account's slot matured recently so the percent has only just begun to decay, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that userRewards must capture every balance-weighted segment even when the global index did not move.
