# Q3028: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
rewards/BaseRewardPoolV2.sol: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. With the victim address and the exact block in which their reward index is snapshotted under attacker control and a reward-manager queueNewRewards transaction is pending in the mempool, can an unprivileged caller sequence `updateFor(address _account)` so that `rewards[_rewardToken].queuedRewards` and `rewards[_rewardToken].rewardPerTokenStored` no longer reconcile, violating the invariant that reward share must be weighted by time held, not by balance at the instant the index moves and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: a reward-manager queueNewRewards transaction is pending in the mempool.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `rewards[_rewardToken].rewardPerTokenStored`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `updateFor(address _account)`: constrain the setup so that a reward-manager queueNewRewards transaction is pending in the mempool, fuzz the attacker inputs (the victim address and the exact block in which their reward index is snapshotted), and assert after every call that reward share must be weighted by time held, not by balance at the instant the index moves.
