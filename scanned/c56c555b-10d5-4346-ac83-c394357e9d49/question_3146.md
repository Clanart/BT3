# Q3146: ReferralStorage.updateTotalFactor - sqrt factor makes many small accounts dominate the denominator

## Question
Consider rewards/ReferralStorage.sol, where userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Assuming the attacker splits one large lock across many addresses that each register a code, can an unprivileged attacker turn this into a divergence between `BoostPoint` and `totalBoostFactor` via `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, breaking the invariant that a boost weight must not reward splitting one position across addresses and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker splits one large lock across many addresses that each register a code, snapshot `BoostPoint` and `totalBoostFactor`, run the attacker's `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
