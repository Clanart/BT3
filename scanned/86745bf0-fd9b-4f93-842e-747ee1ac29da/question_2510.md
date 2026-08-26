# Q2510: ReferralStorage.registerCode - sqrt factor makes many small accounts dominate the denominator

## Question
Consider rewards/ReferralStorage.sol, where userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Assuming the attacker cancels a cooldown so their real lock rises with no factor refresh, can an unprivileged attacker turn this into a divergence between `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` via `registerCode(bytes32 _code)`, breaking the invariant that a boost weight must not reward splitting one position across addresses and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker cancels a cooldown so their real lock rises with no factor refresh, snapshot `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))`, run the attacker's `registerCode(bytes32 _code)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
