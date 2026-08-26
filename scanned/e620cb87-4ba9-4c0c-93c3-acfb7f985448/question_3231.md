# Q3231: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol - userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker controlling which code is bound, and from which of the attacker's own addresses, under the referee has a large pending MGP claim in MasterMagpie, exploit this through `useCode(bytes32 _code)` to break the reconciliation between `codeOwners[_code]` and `userInfos[account].myCode` and the invariant that a boost weight must not reward splitting one position across addresses, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `useCode(bytes32 _code)`: constrain the setup so that the referee has a large pending MGP claim in MasterMagpie, fuzz the attacker inputs (which code is bound, and from which of the attacker's own addresses), and assert after every call that a boost weight must not reward splitting one position across addresses.
