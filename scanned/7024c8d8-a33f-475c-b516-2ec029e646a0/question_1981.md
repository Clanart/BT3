# Q1981: ReferralStorage.updateTotalFactor - registering a code after locking leaves the factor at zero

## Question
Note that in rewards/ReferralStorage.sol, updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Can an attacker holding only tokens bought on market reach it via `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values and force `codeOwners[_code]` apart from `userInfos[account].myCode`, breaking the invariant that the boost denominator must reflect every participant's real lock as soon as they become eligible for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: registering a code after locking leaves the factor at zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: updateTotalFactor() returns immediately when userInfo.myCode is zero, so an account that locks first and registers afterwards is never folded into totalBoostFactor until its next lock, understating the denominator for everyone. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: the boost denominator must reflect every participant's real lock as soon as they become eligible; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, call `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`, and assert `codeOwners[_code]` equals `userInfos[account].myCode` and that no account can withdraw more than it put in.
