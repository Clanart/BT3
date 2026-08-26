# Q1335: ReferralStorage.trigger - both sides of the split are paid so a referral strictly increases total emissions owed

## Question
Consider rewards/ReferralStorage.sol, where trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Assuming BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, can an unprivileged attacker turn this into a divergence between `myReferer[account]` and `userInfos[account].codeIUsed` via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, breaking the invariant that referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: both sides of the split are paid so a referral strictly increases total emissions owed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, snapshot `myReferer[account]` and `userInfos[account].codeIUsed`, run the attacker's `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
