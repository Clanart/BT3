# Q3136: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Assuming the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(mWOMSV).totalSupply()` via `updateFor(address _account)`, breaking the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, snapshot `totalStaked()` and `IERC20(mWOMSV).totalSupply()`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
