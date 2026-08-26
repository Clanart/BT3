# Q3536: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Does `useCode(bytes32 _code)` let an unprivileged caller exploit that under the attacker calls multiclaimFor on a set of referred accounts in one block, so that `myReferer[account]` diverges from `userInfos[account].codeIUsed`, the invariant that a boost weight must not reward splitting one position across addresses is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls multiclaimFor on a set of referred accounts in one block, snapshot `myReferer[account]` and `userInfos[account].codeIUsed`, run the attacker's `useCode(bytes32 _code)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
