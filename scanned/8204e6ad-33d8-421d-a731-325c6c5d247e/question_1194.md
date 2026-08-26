# Q1194: ReferralStorage.registerCode - sqrt factor makes many small accounts dominate the denominator

## Question
Note that in rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an attacker holding only tokens bought on market reach it via `registerCode(bytes32 _code)` under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR and force `BoostPoint` apart from `totalBoostFactor`, breaking the invariant that a boost weight must not reward splitting one position across addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `registerCode(bytes32 _code)` sequence atomically under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, asserting at the end that `BoostPoint` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
