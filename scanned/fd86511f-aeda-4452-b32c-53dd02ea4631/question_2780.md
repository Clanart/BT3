# Q2780: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
In rewards/ReferralStorage.sol, updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Does `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` let an unprivileged caller exploit that under the attacker cancels a cooldown so their real lock rises with no factor refresh, so that `BoostPoint` diverges from `totalBoostFactor`, the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence atomically under the attacker cancels a cooldown so their real lock rises with no factor refresh, asserting at the end that `BoostPoint` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
