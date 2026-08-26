# Q2395: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
rewards/ReferralStorage.sol - updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under the attacker locked vlMGP before registering a code, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `myReferer[account]` and `userInfos[account].codeIUsed` and the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker locked vlMGP before registering a code, then assert `myReferer[account]` and `userInfos[account].codeIUsed` end identical in both runs.
