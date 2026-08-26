# Q0701: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
Consider rewards/ReferralStorage.sol, where userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Assuming the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, can an unprivileged attacker turn this into a divergence between `BoostPoint` and `totalBoostFactor` via `useCode(bytes32 _code)`, breaking the invariant that a boost weight must not reward splitting one position across addresses and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, snapshot `BoostPoint` and `totalBoostFactor`, run the attacker's `useCode(bytes32 _code)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
