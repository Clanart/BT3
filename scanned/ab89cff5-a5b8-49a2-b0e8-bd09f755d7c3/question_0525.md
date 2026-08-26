# Q0525: DSMath.sqrt - sqrt rewards splitting one position across addresses

## Question
libraries/DSMath.sol - ReferralStorage.updateTotalFactor sets userInfo.factor = DSMath.sqrt(lockedAmount), and a concave weight means the sum of square roots of many small locks exceeds the square root of one large lock, so splitting shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker is the only registered participant so totalBoostFactor equals their own factor, exploit this through `sqrt(uint256 y)` to break the reconciliation between `WAD` and `the operand scale used by the caller` and the invariant that a participation weight must not increase when one position is split across addresses controlled by the same actor, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: sqrt rewards splitting one position across addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: ReferralStorage.updateTotalFactor sets userInfo.factor = DSMath.sqrt(lockedAmount), and a concave weight means the sum of square roots of many small locks exceeds the square root of one large lock, so splitting shifts the shared BoostPoint toward the splitter. Precondition: the attacker is the only registered participant so totalBoostFactor equals their own factor.
- Invariant to test: a participation weight must not increase when one position is split across addresses controlled by the same actor; concretely, `WAD` must stay reconciled with `the operand scale used by the caller`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker is the only registered participant so totalBoostFactor equals their own factor, call `sqrt(uint256 y)`, and assert `WAD` equals `the operand scale used by the caller` and that no account can withdraw more than it put in.
