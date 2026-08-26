# Q1625: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
rewards/ReferralStorage.sol: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, is there an unprivileged sequence of `registerCode(bytes32 _code)` that leaves `userInfos[account].rewardAmount` unreconciled with `MGP.balanceOf(address(this))`, violates the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, then assert `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` end identical in both runs.
