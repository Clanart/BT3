# Q2875: ReferralStorage.registerCode - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Under the attacker splits one large lock across many addresses that each register a code, is there an unprivileged sequence of `registerCode(bytes32 _code)` that leaves `refererPercentage + refereePercentage` unreconciled with `DENOMINATOR`, violates the invariant that a boost weight must not reward splitting one position across addresses, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `registerCode(bytes32 _code)`: constrain the setup so that the attacker splits one large lock across many addresses that each register a code, fuzz the attacker inputs (any unclaimed 32-byte code, and how many times registration is repeated), and assert after every call that a boost weight must not reward splitting one position across addresses.
