# Q0866: DSMath.sqrt - fixed point operands are scaled by the caller

## Question
libraries/DSMath.sol - the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker raises and lowers their lock repeatedly across blocks, exploit this through `sqrt(uint256 y)` to break the reconciliation between `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage` and the invariant that the operand range of a fixed point helper must be bounded by protocol invariants, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker raises and lowers their lock repeatedly across blocks.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker raises and lowers their lock repeatedly across blocks, then assert `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage` end identical in both runs.
