# Q3824: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Does `useCode(bytes32 _code)` let an unprivileged caller exploit that under sharePercent is set so most of the split goes to the referee, so that `BoostPoint` diverges from `totalBoostFactor`, the invariant that a boost weight must not reward splitting one position across addresses is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `useCode(bytes32 _code)`: constrain the setup so that sharePercent is set so most of the split goes to the referee, fuzz the attacker inputs (which code is bound, and from which of the attacker's own addresses), and assert after every call that a boost weight must not reward splitting one position across addresses.
