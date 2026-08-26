# Q4040: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
Note that in rewards/ReferralStorage.sol, registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Can an attacker holding only tokens bought on market reach it via `registerCode(bytes32 _code)` under sharePercent is set so most of the split goes to the referrer and force `userInfos[account].factor` apart from `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, breaking the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: sharePercent is set so most of the split goes to the referrer.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under sharePercent is set so most of the split goes to the referrer, then assert `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` end identical in both runs.
