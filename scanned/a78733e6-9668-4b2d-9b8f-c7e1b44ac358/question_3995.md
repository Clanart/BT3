# Q3995: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
rewards/ReferralStorage.sol - updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under sharePercent is set so most of the split goes to the referee, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `refererPercentage + refereePercentage` and `DENOMINATOR` and the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish sharePercent is set so most of the split goes to the referee, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `refererPercentage + refereePercentage` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
