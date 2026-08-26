# Q0556: DSMath.sqrt - sqrt truncation zeroes small participants

## Question
libraries/DSMath.sol - the integer square root truncates, so a lock small enough produces a factor of zero and contributes nothing to totalBoostFactor while still being treated as a registered participant. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker is the only registered participant so totalBoostFactor equals their own factor, exploit this through `sqrt(uint256 y)` to break the reconciliation between `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage` and the invariant that a weight function must not silently reduce a real participant to zero influence, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: sqrt truncation zeroes small participants)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: the integer square root truncates, so a lock small enough produces a factor of zero and contributes nothing to totalBoostFactor while still being treated as a registered participant. Precondition: the attacker is the only registered participant so totalBoostFactor equals their own factor.
- Invariant to test: a weight function must not silently reduce a real participant to zero influence; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the only registered participant so totalBoostFactor equals their own factor, have the attacker run `sqrt(uint256 y)`, then assert the victim's claimable value and the `DSMath.sqrt(lockedAmount)` versus `userInfos[account].factor in ReferralStorage` relation are unchanged by the attacker's transaction.
