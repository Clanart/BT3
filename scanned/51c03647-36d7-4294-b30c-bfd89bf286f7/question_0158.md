# Q0158: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
In rewards/BaseRewardPoolV2.sol, balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Starting from a state where the pool has exactly one registered reward token and no queued backlog, can an unprivileged EOA use `updateFor(address _account)` to leave `10**stakingDecimals()` inconsistent with `totalStaked()`, violating the invariant that reward share must be weighted by time held, not by balance at the instant the index moves and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the pool has exactly one registered reward token and no queued backlog.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool has exactly one registered reward token and no queued backlog, then assert `10**stakingDecimals()` and `totalStaked()` end identical in both runs.
