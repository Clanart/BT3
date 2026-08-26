# Q3197: ReferralStorage.registerCode - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. With any unclaimed 32-byte code, and how many times registration is repeated under attacker control and the referee has a large pending MGP claim in MasterMagpie, can an unprivileged caller sequence `registerCode(bytes32 _code)` so that `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` no longer reconcile, violating the invariant that a boost weight must not reward splitting one position across addresses and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the referee has a large pending MGP claim in MasterMagpie, then assert `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` end identical in both runs.
