# Q0143: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol - userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker controlling which code is bound, and from which of the attacker's own addresses, under the attacker controls two addresses and binds one to the other's code, exploit this through `useCode(bytes32 _code)` to break the reconciliation between `myReferer[account]` and `userInfos[account].codeIUsed` and the invariant that a boost weight must not reward splitting one position across addresses, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker controls two addresses and binds one to the other's code, call `useCode(bytes32 _code)`, and assert `myReferer[account]` equals `userInfos[account].codeIUsed` and that no account can withdraw more than it put in.
