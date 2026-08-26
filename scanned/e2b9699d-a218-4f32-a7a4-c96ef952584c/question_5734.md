# Q5734: MasterMagpie.multiclaimFor - referral trigger fired on attacker-chosen victims

## Question
In rewards/MasterMagpie.sol, _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Can an unprivileged attacker reach this through `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` while the contract is paused so only emergencyWithdraw is reachable, and drive `vlmgp.totalSupply()` out of agreement with `sum of userInfo[vlmgp][*].amount` - breaking the invariant that referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)` (mechanism: referral trigger fired on attacker-chosen victims)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimFor(address[] _stakingTokens, address[][] _rewardTokens, address _account)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _account (any victim), the staking-token list and the per-pool reward-token lists
- Exploit idea: _multiClaim() ends with IReferralStorage(referral).trigger(_user, totalReward) and multiclaimFor lets the attacker pick _user, so referral accrual can be driven for arbitrary accounts at arbitrary times without their consent. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: referral accrual must be a consequence of the referee's own voluntary claim, not of a third party's transaction; concretely, `vlmgp.totalSupply()` must stay reconciled with `sum of userInfo[vlmgp][*].amount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract is paused so only emergencyWithdraw is reachable, then assert `vlmgp.totalSupply()` and `sum of userInfo[vlmgp][*].amount` end identical in both runs.
