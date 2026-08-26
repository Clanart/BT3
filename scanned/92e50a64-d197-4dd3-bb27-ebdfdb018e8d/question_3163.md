# Q3163: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
rewards/ReferralStorage.sol: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. With any unclaimed 32-byte code, and how many times registration is repeated under attacker control and the referee has a large pending MGP claim in MasterMagpie, can an unprivileged caller sequence `registerCode(bytes32 _code)` so that `myReferer[account]` and `userInfos[account].codeIUsed` no longer reconcile, violating the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the referee has a large pending MGP claim in MasterMagpie, have the attacker run `registerCode(bytes32 _code)`, then assert the victim's claimable value and the `myReferer[account]` versus `userInfos[account].codeIUsed` relation are unchanged by the attacker's transaction.
