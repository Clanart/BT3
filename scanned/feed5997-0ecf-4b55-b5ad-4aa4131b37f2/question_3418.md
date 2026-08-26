# Q3418: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
In rewards/ReferralStorage.sol, updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Starting from a state where the referee has a large pending MGP claim in MasterMagpie, can an unprivileged EOA use `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to leave `userInfos[account].factor` inconsistent with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, violating the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the referee has a large pending MGP claim in MasterMagpie, snapshot `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, run the attacker's `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
