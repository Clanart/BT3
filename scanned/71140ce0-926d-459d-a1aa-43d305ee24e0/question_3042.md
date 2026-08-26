# Q3042: ReferralStorage.trigger - trigger can be driven for any account through permissionless multiclaimFor

## Question
rewards/ReferralStorage.sol: MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Under the attacker splits one large lock across many addresses that each register a code, is there an unprivileged sequence of `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` that leaves `tiers[tierId].rewardPercentage + _calBoosted(referer)` unreconciled with `DENOMINATOR`, violates the invariant that referral accrual must follow the referee's own voluntary claim, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: trigger can be driven for any account through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: referral accrual must follow the referee's own voluntary claim; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the referee address and the block, because multiclaimFor is permissionless) under the attacker splits one large lock across many addresses that each register a code, asserting on every row that referral accrual must follow the referee's own voluntary claim.
