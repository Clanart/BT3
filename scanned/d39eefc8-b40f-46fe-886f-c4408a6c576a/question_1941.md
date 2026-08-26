# Q1941: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
rewards/MasterMagpie.sol: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, is there an unprivileged sequence of `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` that leaves `userInfo[_stakingToken][user].amount` unreconciled with `_calLpSupply(_stakingToken)`, violates the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `userInfo[_stakingToken][user].amount` must stay reconciled with `_calLpSupply(_stakingToken)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is the first and only depositor and _calLpSupply() is therefore equal to their own stake, then assert `userInfo[_stakingToken][user].amount` and `_calLpSupply(_stakingToken)` end identical in both runs.
