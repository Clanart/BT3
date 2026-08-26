# Q2242: BaseRewardPoolV2.updateFor - stake, claim and unstake inside one block

## Question
rewards/BaseRewardPoolV2.sol: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, is there an unprivileged sequence of `updateFor(address _account)` that leaves `totalStaked()` unreconciled with `IERC20(stakingToken).balanceOf(operator)`, violates the invariant that reward share must be weighted by time held, not by balance at the instant the index moves, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BaseRewardPoolV2.sol -> `updateFor(address _account)` (mechanism: stake, claim and unstake inside one block)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateFor(address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the victim address and the exact block in which their reward index is snapshotted
- Exploit idea: balanceOf() is an instantaneous read of MasterMagpie's UserInfo.amount with no time weighting, so an attacker can deposit, trigger updateFor/getReward and withdraw inside a single transaction to capture an index jump they contributed nothing to. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small.
- Invariant to test: reward share must be weighted by time held, not by balance at the instant the index moves; concretely, `totalStaked()` must stay reconciled with `IERC20(stakingToken).balanceOf(operator)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the staking token is a low-decimal receipt token so 10**stakingDecimals() is small, have the attacker run `updateFor(address _account)`, then assert the victim's claimable value and the `totalStaked()` versus `IERC20(stakingToken).balanceOf(operator)` relation are unchanged by the attacker's transaction.
