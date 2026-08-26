# Q5382: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
In rewards/MasterMagpie.sol, _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Can an unprivileged attacker reach this through `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` while the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), and drive `IBaseRewardPool(rewarder).balanceOf(user)` out of agreement with `IBaseRewardPool(rewarder).totalStaked()` - breaking the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked().
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `IBaseRewardPool(rewarder).balanceOf(user)` must stay reconciled with `IBaseRewardPool(rewarder).totalStaked()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_account (any victim), the staking-token list and the per-pool reward-token lists) under the staking token is a low-decimal receipt token so 10**stakingDecimals() is small relative to totalStaked(), asserting on every row that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction.
