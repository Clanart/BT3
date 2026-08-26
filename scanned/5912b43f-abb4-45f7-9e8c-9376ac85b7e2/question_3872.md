# Q3872: ReferralStorage.trigger - both sides of the split are paid so a referral strictly increases total emissions owed

## Question
In rewards/ReferralStorage.sol, trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Starting from a state where sharePercent is set so most of the split goes to the referee, can an unprivileged EOA use `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` to leave `codeOwners[_code]` inconsistent with `userInfos[account].myCode`, violating the invariant that referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: both sides of the split are paid so a referral strictly increases total emissions owed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under sharePercent is set so most of the split goes to the referee, then assert `codeOwners[_code]` and `userInfos[account].myCode` end identical in both runs.
