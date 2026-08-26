# Q3760: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
Note that in rewards/ReferralStorage.sol, registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Can an attacker holding only tokens bought on market reach it via `registerCode(bytes32 _code)` under sharePercent is set so most of the split goes to the referee and force `userInfos[account].factor` apart from `totalBoostFactor`, breaking the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `registerCode(bytes32 _code)`: constrain the setup so that sharePercent is set so most of the split goes to the referee, fuzz the attacker inputs (any unclaimed 32-byte code, and how many times registration is repeated), and assert after every call that the code-ownership map and the per-user record must never disagree about which code an account owns.
