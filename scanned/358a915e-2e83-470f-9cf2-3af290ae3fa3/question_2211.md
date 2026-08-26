# Q2211: ReferralStorage.trigger - both sides of the split are paid so a referral strictly increases total emissions owed

## Question
Note that in rewards/ReferralStorage.sol, trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Can an attacker holding only tokens bought on market reach it via `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` under the attacker locked vlMGP before registering a code and force `userInfos[account].factor` apart from `totalBoostFactor`, breaking the invariant that referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance for Critical - Protocol insolvency?

## Target
- File/function: rewards/ReferralStorage.sol -> `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor` (mechanism: both sides of the split are paid so a referral strictly increases total emissions owed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `trigger(address _referee, uint256 _amount) via MasterMagpie.multiclaimFor`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the referee address and the block, because multiclaimFor is permissionless
- Exploit idea: trigger() credits refererAmount to the referrer and refereeAmount to the referee, and both are computed as a percentage on top of the referee's claim rather than out of it, so every referred claim increases the MGP the contract owes without increasing what it holds. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: referral accrual must be funded from a source that grows with it, not added on top of an unfunded balance; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locked vlMGP before registering a code, then assert `userInfos[account].factor` and `totalBoostFactor` end identical in both runs.
