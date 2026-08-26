# Q3935: ReferralStorage.trigger - trigger can be driven for any account through permissionless multiclaimFor

## Question
rewards/ReferralStorage.sol - MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under sharePercent is set so most of the split goes to the referee, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `BoostPoint` and `totalBoostFactor` and the invariant that referral accrual must follow the referee's own voluntary claim, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: trigger can be driven for any account through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: referral accrual must follow the referee's own voluntary claim; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the referee address and the block, because multiclaimFor is permissionless) under sharePercent is set so most of the split goes to the referee, asserting on every row that referral accrual must follow the referee's own voluntary claim.
