# Q2107: vlMGPBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
In rewards/vlMGPBaseRewarder.sol, because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Does `updateFor(address _account)` let an unprivileged caller exploit that under the computed forfeit lands just above the _amount / 1000 dust threshold, so that `_calExpireForfeit(account,_amount)` diverges from `vlMGP.getRewardablePercentWAD(account)`, the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the computed forfeit lands just above the _amount / 1000 dust threshold.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `_calExpireForfeit(account,_amount)` must stay reconciled with `vlMGP.getRewardablePercentWAD(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the computed forfeit lands just above the _amount / 1000 dust threshold, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `_calExpireForfeit(account,_amount)` versus `vlMGP.getRewardablePercentWAD(account)` relation are unchanged by the attacker's transaction.
