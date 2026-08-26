# Q4146: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
rewards/MasterMagpie.sol - _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Can an unprivileged attacker controlling _account (any victim), the staking-token list and the per-pool reward-token lists, under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, exploit this through `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` to break the reconciliation between `unClaimedMgp[_stakingToken][user]` and `userInfo[_stakingToken][user].rewardDebt` and the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `unClaimedMgp[_stakingToken][user]` must stay reconciled with `userInfo[_stakingToken][user].rewardDebt`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_account (any victim), the staking-token list and the per-pool reward-token lists) under the pool's rewarder is a V1 rewards/BaseRewardPool.sol whose getRewards body is empty, asserting on every row that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction.
