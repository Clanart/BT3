# Q1134: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
rewards/ReferralStorage.sol - registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Can an unprivileged attacker controlling any unclaimed 32-byte code, and how many times registration is repeated, under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, exploit this through `registerCode(bytes32 _code)` to break the reconciliation between `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` and the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, call `registerCode(bytes32 _code)`, and assert `userInfos[account].factor` equals `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` and that no account can withdraw more than it put in.
