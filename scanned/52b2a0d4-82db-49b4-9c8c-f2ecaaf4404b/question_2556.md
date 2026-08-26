# Q2556: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. With which code is bound, and from which of the attacker's own addresses under attacker control and the attacker cancels a cooldown so their real lock rises with no factor refresh, can an unprivileged caller sequence `useCode(bytes32 _code)` so that `refererPercentage + refereePercentage` and `DENOMINATOR` no longer reconcile, violating the invariant that a boost weight must not reward splitting one position across addresses and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `useCode(bytes32 _code)` sequence atomically under the attacker cancels a cooldown so their real lock rises with no factor refresh, asserting at the end that `refererPercentage + refereePercentage` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
