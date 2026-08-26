# Q1021: DSMath.sqrt - sqrt rewards splitting one position across addresses

## Question
libraries/DSMath.sol - ReferralStorage.updateTotalFactor sets userInfo.factor = DSMath.sqrt(lockedAmount), and a concave weight means the sum of square roots of many small locks exceeds the square root of one large lock, so splitting shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under BoostPoint is configured at a large fraction of DENOMINATOR, exploit this through `sqrt(uint256 y)` to break the reconciliation between `userInfos[account].factor` and `totalBoostFactor` and the invariant that a participation weight must not increase when one position is split across addresses controlled by the same actor, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: sqrt rewards splitting one position across addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: ReferralStorage.updateTotalFactor sets userInfo.factor = DSMath.sqrt(lockedAmount), and a concave weight means the sum of square roots of many small locks exceeds the square root of one large lock, so splitting shifts the shared BoostPoint toward the splitter. Precondition: BoostPoint is configured at a large fraction of DENOMINATOR.
- Invariant to test: a participation weight must not increase when one position is split across addresses controlled by the same actor; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `sqrt(uint256 y)`: constrain the setup so that BoostPoint is configured at a large fraction of DENOMINATOR, fuzz the attacker inputs (the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking), and assert after every call that a participation weight must not increase when one position is split across addresses controlled by the same actor.
