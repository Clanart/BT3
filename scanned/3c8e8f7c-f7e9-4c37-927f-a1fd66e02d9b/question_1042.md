# Q1042: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
In rewards/ReferralStorage.sol, updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Starting from a state where the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, can an unprivileged EOA use `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to leave `refererPercentage + refereePercentage` inconsistent with `DENOMINATOR`, violating the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, have the attacker run `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, then assert the victim's claimable value and the `refererPercentage + refereePercentage` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
