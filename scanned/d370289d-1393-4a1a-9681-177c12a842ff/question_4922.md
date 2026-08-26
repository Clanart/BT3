# Q4922: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
rewards/mWOMSVBaseRewarder.sol - because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Can an unprivileged attacker controlling the victim address and the block at which their index is pinned, under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, exploit this through `updateFor(address _account)` to break the reconciliation between `userRewards[_rewardToken][account]` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the attacker settles the same reward token through two separate multiclaimSpec calls in one block.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under the attacker settles the same reward token through two separate multiclaimSpec calls in one block, asserting on every row that a forfeit redistribution must be weighted by the time stakers were actually committed.
