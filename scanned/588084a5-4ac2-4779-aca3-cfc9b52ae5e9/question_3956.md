# Q3956: mWOMSVBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
Note that in rewards/mWOMSVBaseRewarder.sol, because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Can an attacker holding only tokens bought on market reach it via `updateFor(address _account)` under the attacker locks one block before a known large settlement and unlocks one block after and force `forfeitAmount` apart from `rewardInfo.rewardPerTokenStored`, breaking the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed for Critical - Direct theft of user funds?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locks one block before a known large settlement and unlocks one block after, then assert `forfeitAmount` and `rewardInfo.rewardPerTokenStored` end identical in both runs.
