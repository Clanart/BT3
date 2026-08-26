# Q0773: DSMath.sqrt - sqrt rewards splitting one position across addresses

## Question
libraries/DSMath.sol - ReferralStorage.updateTotalFactor sets userInfo.factor = DSMath.sqrt(lockedAmount), and a concave weight means the sum of square roots of many small locks exceeds the square root of one large lock, so splitting shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker controlling the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking, under the attacker raises and lowers their lock repeatedly across blocks, exploit this through `sqrt(uint256 y)` to break the reconciliation between `DSMath.sqrt(lockedAmount)` and `userInfos[account].factor in ReferralStorage` and the invariant that a participation weight must not increase when one position is split across addresses controlled by the same actor, yielding High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `sqrt(uint256 y)` (mechanism: sqrt rewards splitting one position across addresses)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `sqrt(uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the locked vlMGP amount fed in by ReferralStorage.updateTotalFactor, which the attacker sets by locking and unlocking
- Exploit idea: ReferralStorage.updateTotalFactor sets userInfo.factor = DSMath.sqrt(lockedAmount), and a concave weight means the sum of square roots of many small locks exceeds the square root of one large lock, so splitting shifts the shared BoostPoint toward the splitter. Precondition: the attacker raises and lowers their lock repeatedly across blocks.
- Invariant to test: a participation weight must not increase when one position is split across addresses controlled by the same actor; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker raises and lowers their lock repeatedly across blocks, have the attacker run `sqrt(uint256 y)`, then assert the victim's claimable value and the `DSMath.sqrt(lockedAmount)` versus `userInfos[account].factor in ReferralStorage` relation are unchanged by the attacker's transaction.
