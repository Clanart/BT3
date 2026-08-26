# Q0219: BaseRewardPool.updateFor - stake, claim and unstake inside one block

## Question
In rewards/BaseRewardPool.sol, balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Can an unprivileged attacker reach this through `updateFor(address _account)` while the pool has exactly one registered reward token and no queued backlog, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that reward share must be weighted by time held, not by balance at the instant the index moves - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool has exactly one registered reward token and no queued backlog, call `updateFor(address _account)`, and assert `10**stakingDecimals()` equals `totalStaked()` and that no account can withdraw more than it put in.
