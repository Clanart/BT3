# Q0577: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
rewards/ReferralStorage.sol: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, is there an unprivileged sequence of `registerCode(bytes32 _code)` that leaves `userInfos[account].factor` unreconciled with `totalBoostFactor`, violates the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, then assert `userInfos[account].factor` and `totalBoostFactor` end identical in both runs.
