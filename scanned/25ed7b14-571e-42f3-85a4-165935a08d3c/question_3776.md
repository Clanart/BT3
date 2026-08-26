# Q3776: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
rewards/ReferralStorage.sol: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Under sharePercent is set so most of the split goes to the referee, is there an unprivileged sequence of `registerCode(bytes32 _code)` that leaves `userInfos[account].factor` unreconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, violates the invariant that each account must own at most one code and every code must resolve consistently in both directions, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish sharePercent is set so most of the split goes to the referee, have the attacker run `registerCode(bytes32 _code)`, then assert the victim's claimable value and the `userInfos[account].factor` versus `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))` relation are unchanged by the attacker's transaction.
