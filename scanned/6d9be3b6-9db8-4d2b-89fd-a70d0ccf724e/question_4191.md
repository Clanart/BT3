# Q4191: BaseRewardPool.updateFor - stake, claim and unstake inside one block

## Question
rewards/BaseRewardPool.sol - balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Can an unprivileged attacker controlling the victim address and the exact block in which their reward index is snapshotted, under the victim has not been settled for several epochs and holds a large userRewards balance, exploit this through `updateFor(address _account)` to break the reconciliation between `10**stakingDecimals()` and `totalStaked()` and the invariant that reward share must be weighted by time held, not by balance at the instant the index moves, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPool.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the victim has not been settled for several epochs and holds a large userRewards balance, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `10**stakingDecimals()` versus `totalStaked()` relation are unchanged by the attacker's transaction.
