# Q0781: vlMGPBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
rewards/vlMGPBaseRewarder.sol: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Under the account's slot matured recently so the percent has only just begun to decay, is there an unprivileged sequence of `updateFor(address _account)` that leaves `forfeitAmount` unreconciled with `rewardInfo.rewardPerTokenStored`, violates the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the account's slot matured recently so the percent has only just begun to decay.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `forfeitAmount` must stay reconciled with `rewardInfo.rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the account's slot matured recently so the percent has only just begun to decay, snapshot `forfeitAmount` and `rewardInfo.rewardPerTokenStored`, run the attacker's `updateFor(address _account)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
