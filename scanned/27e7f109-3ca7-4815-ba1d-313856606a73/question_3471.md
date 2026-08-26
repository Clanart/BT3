# Q3471: BaseRewardPool.updateFor - stake, claim and unstake inside one block

## Question
rewards/BaseRewardPool.sol - balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under a reward-manager queueNewRewards transaction is pending in the mempool, exploit this through `updateFor(address _account)` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` and the invariant that reward share must be weighted by time held, not by balance at the instant the index moves, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `updateFor(address _account)` sequence atomically under a reward-manager queueNewRewards transaction is pending in the mempool, asserting at the end that `rewards[_rewardToken].queuedRewards` still equals `rewards[_rewardToken].rewardPerTokenStored` and the PoC's balance delta is non-positive.
