# Q0007: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
rewards/mWOMSVBaseRewarder.sol: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. With the victim address and the block at which their index is pinned under attacker control and the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, can an unprivileged caller sequence `updateFor(address _account)` so that `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` no longer reconcile, violating the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that a forfeit redistribution must be weighted by the time stakers were actually committed.
