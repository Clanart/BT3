# Q2669: vlMGPBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
rewards/vlMGPBaseRewarder.sol: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Under a large MGP distribution has just been queued and no account has settled yet, is there an unprivileged sequence of `updateFor(address _account)` that leaves `userRewards[_rewardToken][account]` unreconciled with `rewards[_rewardToken].rewardPerTokenStored`, violates the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: a large MGP distribution has just been queued and no account has settled yet.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large MGP distribution has just been queued and no account has settled yet, call `updateFor(address _account)`, and assert `userRewards[_rewardToken][account]` equals `rewards[_rewardToken].rewardPerTokenStored` and that no account can withdraw more than it put in.
