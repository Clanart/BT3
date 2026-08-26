# Q0006: vlMGPBaseRewarder.updateFor - forfeit recycling front-run by a one-block staker

## Question
In rewards/vlMGPBaseRewarder.sol, because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Can an unprivileged attacker reach this through `updateFor(address _account)` while the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, and drive `balanceOf(account)` out of agreement with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` - breaking the invariant that a forfeit redistribution must be weighted by the time stakers were actually committed - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/vlMGPBaseRewarder.sol -> `updateFor(address _account)` (mechanism: forfeit recycling front-run by a one-block staker)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the block at which their index is pinned
- Exploit idea: because the forfeit is folded into rewardPerTokenStored immediately and balanceOf() is an instantaneous read of MasterMagpie stake, an attacker who locks just before a large forfeit settlement captures a share of it with no time weighting. Precondition: the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: a forfeit redistribution must be weighted by the time stakers were actually committed; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account is inside its cooldown window so getRewardablePercentWAD is exactly 1e18, call `updateFor(address _account)`, and assert `balanceOf(account)` equals `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,account).staked` and that no account can withdraw more than it put in.
