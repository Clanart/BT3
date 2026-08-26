# Q3712: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
rewards/ReferralStorage.sol: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. With the target account, because lockFor is permissionless under attacker control and the attacker calls multiclaimFor on a set of referred accounts in one block, can an unprivileged caller sequence `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` so that `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` no longer reconcile, violating the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls multiclaimFor on a set of referred accounts in one block, call `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, and assert `userInfos[account].rewardAmount` equals `MGP.balanceOf(address(this))` and that no account can withdraw more than it put in.
