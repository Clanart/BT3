# Q0639: ReferralStorage.registerCode - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Starting from a state where the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, can an unprivileged EOA use `registerCode(bytes32 _code)` to leave `myReferer[account]` inconsistent with `userInfos[account].codeIUsed`, violating the invariant that a boost weight must not reward splitting one position across addresses and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, call `registerCode(bytes32 _code)`, and assert `myReferer[account]` equals `userInfos[account].codeIUsed` and that no account can withdraw more than it put in.
