# Q1797: ReferralStorage.trigger - both sides of the split are paid so a referral strictly increases total emissions owed

## Question
In rewards/ReferralStorage.sol, trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Can an unprivileged attacker reach this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` while the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, and drive `BoostPoint` out of agreement with `totalBoostFactor` - breaking the invariant that referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance - for Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: both sides of the split are paid so a referral strictly increases total emissions owed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, call `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`, and assert `BoostPoint` equals `totalBoostFactor` and that no account can withdraw more than it put in.
