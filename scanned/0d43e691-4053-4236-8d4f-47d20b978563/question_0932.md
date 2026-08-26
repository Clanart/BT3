# Q0932: BaseRewardPool.updateFor - stake, claim and unstake inside one block

## Question
rewards/BaseRewardPool.sol: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. With the victim address and the exact block in which their reward index is snapshotted under attacker control and rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, can an unprivileged caller sequence `updateFor(address _account)` so that `rewardTokens.length` and `isRewardToken[_rewardToken]` no longer reconcile, violating the invariant that reward share must be weighted by time held, not by balance at the instant the index moves and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `rewardTokens.length` must stay reconciled with `isRewardToken[_rewardToken]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewards[token].queuedRewards holds a large backlog accumulated while totalStaked() was zero, call `updateFor(address _account)`, and assert `rewardTokens.length` equals `isRewardToken[_rewardToken]` and that no account can withdraw more than it put in.
