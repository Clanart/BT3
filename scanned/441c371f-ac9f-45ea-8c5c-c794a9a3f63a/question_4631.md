# Q4631: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
rewards/mWOMSVBaseRewarder.sol: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. With the victim address and the block at which their index is pinned under attacker control and a registered reward token has begun reverting on transfer, can an unprivileged caller sequence `updateFor(address _account)` so that `_calExpireForfeit(account,_amount)` and `mWOMSV.getRewardablePercentWAD(account)` no longer reconcile, violating the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: a registered reward token has begun reverting on transfer.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `mWOMSV.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a registered reward token has begun reverting on transfer, call `updateFor(address _account)`, and assert `_calExpireForfeit(account,_amount)` equals `mWOMSV.getRewardablePercentWAD(account)` and that no account can withdraw more than it put in.
