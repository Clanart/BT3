# Q3559: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
rewards/mWOMSVBaseRewarder.sol: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. With the victim address and the block at which their index is pinned under attacker control and totalStaked is zero and queuedRewards holds a backlog, can an unprivileged caller sequence `updateFor(address _account)` so that `balanceOf(account)` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` no longer reconcile, violating the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: totalStaked is zero and queuedRewards holds a backlog.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under totalStaked is zero and queuedRewards holds a backlog, asserting at the end that `balanceOf(account)` still equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` and the PoC's balance delta is non-positive.
