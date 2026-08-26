# Q2951: ReferralStorage.claimReward - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Does `claimReward()` let an unprivileged caller exploit that under the attacker splits one large lock across many addresses that each register a code, so that `codeOwners[_code]` diverges from `userInfos[account].myCode`, the invariant that a boost weight must not reward splitting one position across addresses is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `claimReward()` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimReward()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the moment the accrued MGP is drawn from the shared contract balance
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker splits one large lock across many addresses that each register a code, call `claimReward()`, and assert `codeOwners[_code]` equals `userInfos[account].myCode` and that no account can withdraw more than it put in.
