# Q1547: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
rewards/ReferralStorage.sol - updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Can an unprivileged attacker controlling the target account, because lockFor is permissionless, under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, exploit this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` to break the reconciliation between `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR` and the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, snapshot `tiers[tierId].rewardPercentage + _calBoosted(referer)` and `DENOMINATOR`, run the attacker's `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
