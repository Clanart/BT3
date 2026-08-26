# Q1510: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
In rewards/mWOMSVBaseRewarder.sol, because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Can an unprivileged attacker reach this through `updateFor(address _account)` while the computed forfeit lands just below the _amount / 1000 dust threshold, and drive `rewards[_rewardToken].historicalRewards` out of agreement with `IERC20(_rewardToken).balanceOf(address(this))` - breaking the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the computed forfeit lands just below the _amount / 1000 dust threshold.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `rewards[_rewardToken].historicalRewards` must stay reconciled with `IERC20(_rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the computed forfeit lands just below the _amount / 1000 dust threshold, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that a forfeit redistribution must be weighted by the time stakers were actually committed.
