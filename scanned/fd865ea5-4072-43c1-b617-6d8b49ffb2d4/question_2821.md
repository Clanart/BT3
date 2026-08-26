# Q2821: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
rewards/MasterMagpie.sol: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Under the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, is there an unprivileged sequence of `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` that leaves `userInfo[_stakingToken][user].available` unreconciled with `userInfo[_stakingToken][user].amount`, violates the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the attacker holds one wei of stake so lpSupply is non-zero but every division truncates.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `userInfo[_stakingToken][user].available` must stay reconciled with `userInfo[_stakingToken][user].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`: constrain the setup so that the attacker holds one wei of stake so lpSupply is non-zero but every division truncates, fuzz the attacker inputs (_account (any victim), the staking-token list and the per-pool reward-token lists), and assert after every call that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction.
