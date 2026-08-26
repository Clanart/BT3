# Q2701: ReferralStorage.trigger - trigger can be driven for any account through permissionless multiclaimFor

## Question
Consider rewards/ReferralStorage.sol, where MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Assuming the attacker cancels a cooldown so their real lock rises with no factor refresh, can an unprivileged attacker turn this into a divergence between `refererPercentage + refereePercentage` and `DENOMINATOR` via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, breaking the invariant that referral accrual must follow the referee's own voluntary claim and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: trigger can be driven for any account through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: referral accrual must follow the referee's own voluntary claim; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker cancels a cooldown so their real lock rises with no factor refresh, snapshot `refererPercentage + refereePercentage` and `DENOMINATOR`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
