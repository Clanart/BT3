# Q2666: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
rewards/BaseRewardPoolV2.sol: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, is there an unprivileged sequence of `updateFor(address _account)` that leaves `balanceOf(account)` unreconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`, violates the invariant that reward share must be weighted by time held, not by balance at the instant the index moves, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `balanceOf(account)` must stay reconciled with `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator is MasterMagpie and the pool is a vlMGP-style pool credited with no token transfer, then assert `balanceOf(account)` and `IMasterMagpie(operator).stakingInfo(stakingToken,account).staked` end identical in both runs.
