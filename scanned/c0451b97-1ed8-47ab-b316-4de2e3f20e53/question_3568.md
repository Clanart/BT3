# Q3568: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
Note that in rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an attacker holding only tokens bought on market reach it via `claimReward()` under the attacker calls multiclaimFor on a set of referred accounts in one block and force `BoostPoint` apart from `totalBoostFactor`, breaking the invariant that a boost weight must not reward splitting one position across addresses for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claimReward()` sequence atomically under the attacker calls multiclaimFor on a set of referred accounts in one block, asserting at the end that `BoostPoint` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
