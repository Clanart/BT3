# Q0019: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
rewards/ReferralStorage.sol: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. With any unclaimed 32-byte code, and how many times registration is repeated under attacker control and the attacker controls two addresses and binds one to the other's code, can an unprivileged caller sequence `registerCode(bytes32 _code)` so that `BoostPoint` and `totalBoostFactor` no longer reconcile, violating the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: the attacker controls two addresses and binds one to the other's code.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `registerCode(bytes32 _code)` sequence atomically under the attacker controls two addresses and binds one to the other's code, asserting at the end that `BoostPoint` still equals `totalBoostFactor` and the PoC's balance delta is non-positive.
