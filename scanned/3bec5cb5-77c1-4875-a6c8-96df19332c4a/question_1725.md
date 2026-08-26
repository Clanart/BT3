# Q1725: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
Consider rewards/ReferralStorage.sol, where userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Assuming the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, can an unprivileged attacker turn this into a divergence between `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` via `useCode(bytes32 _code)`, breaking the invariant that a boost weight must not reward splitting one position across addresses and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `useCode(bytes32 _code)` sequence atomically under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, asserting at the end that `userInfos[account].factor` still equals `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` and the PoC's balance delta is non-positive.
