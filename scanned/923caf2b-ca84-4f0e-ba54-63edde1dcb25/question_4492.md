# Q4492: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
rewards/BaseRewardPoolV2.sol: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. With the victim address and the exact block in which their reward index is snapshotted under attacker control and the attacker calls the function twice in the same block to observe the second, early-continued iteration, can an unprivileged caller sequence `updateFor(address _account)` so that `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` no longer reconcile, violating the invariant that reward share must be weighted by time held, not by balance at the instant the index moves and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the attacker calls the function twice in the same block to observe the second, early-continued iteration.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls the function twice in the same block to observe the second, early-continued iteration, call `updateFor(address _account)`, and assert `userRewards[_rewardToken][account]` equals `earned(account,_rewardToken)` and that no account can withdraw more than it put in.
