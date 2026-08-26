# Q4145: ReferralStorage.trigger - both sides of the split are paid so a referral strictly increases total emissions owed

## Question
rewards/ReferralStorage.sol - trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Can an unprivileged attacker controlling the referee address and the block, because multiclaimFor is permissionless, under sharePercent is set so most of the split goes to the referrer, exploit this through `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to break the reconciliation between `myReferer[account]` and `userInfos[account].codeIUsed` and the invariant that referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance, yielding Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: both sides of the split are paid so a referral strictly increases total emissions owed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Precondition: sharePercent is set so most of the split goes to the referrer.
- Invariant to test: referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` sequence atomically under sharePercent is set so most of the split goes to the referrer, asserting at the end that `myReferer[account]` still equals `userInfos[account].codeIUsed` and the PoC's balance delta is non-positive.
