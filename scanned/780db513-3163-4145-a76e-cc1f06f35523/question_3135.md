# Q3135: vlMGPBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
rewards/vlMGPBaseRewarder.sol: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. With the victim address and the block at which their index is pinned under attacker control and the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged caller sequence `updateFor(address _account)` so that `totalStaked()` and `IERC20(vlMGP).totalSupply()` no longer reconcile, violating the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `totalStaked()` must stay reconciled with `IERC20(vlMGP).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting on every row that a forfeit redistribution must be weighted by the time stakers were actually committed.
