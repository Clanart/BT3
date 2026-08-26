# Q2729: vlMGPBaseRewarder.updateFor - early-continue skips a real balance change

## Question
Consider rewards/vlMGPBaseRewarder.sol, where _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Assuming a large MGP distribution has just been queued and no account has settled yet, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(vlMGP).totalSupply()` via `updateFor(address _account)`, breaking the invariant that userRewards must capture every balance-weighted segment even when the global index did not move and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: early-continue skips a real balance change)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: _updateFor() continues past a reward token whenever userRewardPerTokenPaid equals rewardPerTokenStored, but balanceOf() is read live, so a lock or unlock that happened while the index stood still is never folded into userRewards. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: userRewards must capture every balance-weighted segment even when the global index did not move; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish a large MGP distribution has just been queued and no account has settled yet, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(vlMGP).totalSupply()` relation are unchanged by the attacker's transaction.
