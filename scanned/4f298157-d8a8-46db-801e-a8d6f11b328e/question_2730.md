# Q2730: mWOMSVBaseRewarder.updateFor - early-continue skips a real balance change

## Question
In rewards/mWOMSVBaseRewarder.sol, _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Starting from a state where a large MGP distribution has just been queued and no account has settled yet, can an unprivileged EOA use `updateFor(address _account)` to leave `totalStaked()` inconsistent with `IERC20(mWOMSV).totalSupply()`, violating the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under a large MGP distribution has just been queued and no account has settled yet, asserting on every row that userRewards must capture every balance-weighted segment even when the global index did not move.
