# Q2108: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the computed forfeit lands just above the _amount / 1000 dust threshold and force `_calExpireForfeit(account,_amount)` apart from `mWOMSV.getRewardablePercentWAD(account)`, breaking the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed for Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the block at which their index is pinned) under the computed forfeit lands just above the _amount / 1000 dust threshold, asserting on every row that a forfeit redistribution must be weighted by the time stakers were actually committed.
