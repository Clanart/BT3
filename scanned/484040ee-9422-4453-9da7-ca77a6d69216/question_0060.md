# Q0060: DSMath.sqrt - sqrt truncation zeroes small participants

## Question
libraries/DSMath.sol - the integer square root truncates, so a lock small enough produces a factor of zero and contributes nothing to totalBoostFactor while still being treated as a registered participant. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker splits one lock across many addresses that each register a referral code, exploit this through `sqrt(uint256 y)` to break the reconciliation between `userInfos[account].factor` and `totalBoostFactor` and the invariant that a weight function must not silently reduce a real participant to zero influence, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: sqrt truncation zeroes small participants)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the integer square root truncates, so a lock small enough produces a factor of zero and contributes nothing to totalBoostFactor while still being treated as a registered participant. Precondition: the attacker splits one lock across many addresses that each register a referral code.
- Invariant to test: a weight function must not silently reduce a real participant to zero influence; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `sqrt(uint256 y)` sequence atomically under the attacker splits one lock across many addresses that each register a referral code, asserting at the end that `userInfos[account].factor` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
