# Q0122: DSMath.sqrt - fixed point operands are scaled by the caller

## Question
libraries/DSMath.sol - the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker splits one lock across many addresses that each register a referral code, exploit this through `sqrt(uint256 y)` to break the reconciliation between `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage` and the invariant that the operand range of a fixed point helper must be bounded by protocol invariants, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: fixed point operands are scaled by the caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the WAD-scaled helpers are applied to amounts the caller sizes directly, so the point at which a product overflows or a quotient truncates is chosen by the caller rather than bounded by the protocol. Precondition: the attacker splits one lock across many addresses that each register a referral code.
- Invariant to test: the operand range of a fixed point helper must be bounded by protocol invariants; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker splits one lock across many addresses that each register a referral code, have the attacker run `sqrt(uint256 y)`, then assert the victim's claimable value and the `DSMath.sqrt(lockedAmount)` versus `userInfos[account].factor in ReferralStorage` relation are unchanged by the attacker's transaction.
