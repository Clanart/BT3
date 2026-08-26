# Q4954: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
In rewards/vlMGPBaseRewarder.sol, _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Starting from a state where the attacker settles the same reward token through two separate multiclaimSpec calls in one block, can an unprivileged EOA use `updateFor(address _account)` to leave `totalStaked()` inconsistent with `IERC20(vlMGP).totalSupply()`, violating the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, then assert `totalStaked()` and `IERC20(vlMGP).totalSupply()` end identical in both runs.
