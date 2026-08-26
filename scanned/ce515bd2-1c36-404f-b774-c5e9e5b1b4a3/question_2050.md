# Q2050: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
In rewards/ReferralStorage.sol, registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Can an unprivileged attacker reach this through `registerCode(bytes32 _code)` while the attacker locked vlMGP before registering a code, and drive `refererPercentage + refereePercentage` out of agreement with `DENOMINATOR` - breaking the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `registerCode(bytes32 _code)`: constrain the setup so that the attacker locked vlMGP before registering a code, fuzz the attacker inputs (any unclaimed 32-byte code, and how many times registration is repeated), and assert after every call that the code-ownership map and the per-user record must never disagree about which code an account owns.
