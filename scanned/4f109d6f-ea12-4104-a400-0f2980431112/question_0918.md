# Q0918: ReferralStorage.trigger - trigger can be driven for any account through permissionless multiclaimFor

## Question
In rewards/ReferralStorage.sol, MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Starting from a state where the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, can an unprivileged EOA use `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to leave `BoostPoint` inconsistent with `totalBoostFactor`, violating the invariant that referral accrual must follow the referee's own voluntary claim and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: trigger can be driven for any account through permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: MasterMagpie.multiclaimFor lets any address force a settlement on any account, and _multiClaim ends by calling trigger(_user, totalReward), so referral accrual can be driven on schedule by a third party. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: referral accrual must follow the referee's own voluntary claim; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, have the attacker run `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, then assert the victim's claimable value and the `BoostPoint` versus `totalBoostFactor` relation are unchanged by the attacker's transaction.
