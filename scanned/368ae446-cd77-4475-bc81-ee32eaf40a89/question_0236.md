# Q0236: ReferralStorage.trigger - both sides of the split are paid so a referral strictly increases total emissions owed

## Question
rewards/ReferralStorage.sol: trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Under the attacker controls two addresses and binds one to the other's code, is there an unprivileged sequence of `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` that leaves `tiers[tierId].rewardPercentage + _calBoosted(referer)` unreconciled with `DENOMINATOR`, violates the invariant that referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: both sides of the split are paid so a referral strictly increases total emissions owed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker controls two addresses and binds one to the other's code, have the attacker run `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, then assert the victim's claimable value and the `tiers[tierId].rewardPercentage + _calBoosted(referer)` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
