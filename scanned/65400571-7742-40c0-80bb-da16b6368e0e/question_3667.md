# Q3667: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
In rewards/BaseRewardPoolV2.sol, balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Can an unprivileged attacker reach this through `updateFor(address _account)` while the victim has not been settled for several epochs and holds a large userRewards balance, and drive `10**stakingDecimals()` out of agreement with `totalStaked()` - breaking the invariant that reward share must be weighted by time held, not by balance at the instant the index moves - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the victim has not been settled for several epochs and holds a large userRewards balance.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `10**stakingDecimals()` must stay reconciled with `totalStaked()`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the victim address and the exact block in which their reward index is snapshotted) under the victim has not been settled for several epochs and holds a large userRewards balance, asserting on every row that reward share must be weighted by time held, not by balance at the instant the index moves.
