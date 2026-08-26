# Q2913: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
Note that in rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an attacker holding only tokens bought on market reach it via `useCode(bytes32 _code)` under the attacker splits one large lock across many addresses that each register a code and force `tiers[tierId].rewardPercentage + _calBoosted(referer)` apart from `DENOMINATOR`, breaking the invariant that a boost weight must not reward splitting one position across addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker splits one large lock across many addresses that each register a code, have the attacker run `useCode(bytes32 _code)`, then assert the victim's claimable value and the `tiers[tierId].rewardPercentage + _calBoosted(referer)` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
