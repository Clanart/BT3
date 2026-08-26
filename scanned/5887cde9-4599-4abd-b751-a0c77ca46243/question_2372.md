# Q2372: ReferralStorage.updateTotalFactor - cancelUnlock raises the real lock while the stored factor stays low

## Question
rewards/ReferralStorage.sol - cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under the attacker locked vlMGP before registering a code, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `codeOwners[_code]` and `userInfos[account].myCode` and the invariant that totalBoostFactor must equal the sum of current per-user factors at all times, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: cancelUnlock raises the real lock while the stored factor stays low)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: cancelUnlock() zeroes a cooldown slot, which immediately raises getUserTotalLocked, but never calls updateTotalFactor, so totalBoostFactor understates true participation and inflates every other referrer's _calBoosted share. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: totalBoostFactor must equal the sum of current per-user factors at all times; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locked vlMGP before registering a code, snapshot `codeOwners[_code]` and `userInfos[account].myCode`, run the attacker's `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
