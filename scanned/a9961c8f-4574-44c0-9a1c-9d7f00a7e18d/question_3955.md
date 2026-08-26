# Q3955: vlMGPBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
In rewards/vlMGPBaseRewarder.sol, because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Does `updateFor(address _account)` let an unprivileged caller exploit that under the attacker locks one block before a known large settlement and unlocks one block after, so that `forfeitAmount` diverges from `rewardInfo.rewardPerTokenStored`, the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the attacker locks one block before a known large settlement and unlocks one block after.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that the attacker locks one block before a known large settlement and unlocks one block after, fuzz the attacker inputs (the victim address and the block at which their index is pinned), and assert after every call that a forfeit redistribution must be weighted by the time stakers were actually committed.
