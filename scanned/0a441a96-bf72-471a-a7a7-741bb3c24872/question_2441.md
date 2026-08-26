# Q2441: ReferralStorage.updateTotalFactor - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. With the target account, because lockFor is permissionless under attacker control and the attacker locked vlMGP before registering a code, can an unprivileged caller sequence `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` so that `codeOwners[_code]` and `userInfos[account].myCode` no longer reconcile, violating the invariant that a boost weight must not reward splitting one position across addresses and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locked vlMGP before registering a code, snapshot `codeOwners[_code]` and `userInfos[account].myCode`, run the attacker's `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
