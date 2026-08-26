# Q0608: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
rewards/ReferralStorage.sol: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, is there an unprivileged sequence of `registerCode(bytes32 _code)` that leaves `userInfos[account].factor` unreconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, violates the invariant that each account must own at most one code and every code must resolve consistently in both directions, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `userInfos[account].factor` must stay reconciled with `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, snapshot `userInfos[account].factor` and `DSMath.sqrt(IVLMGP(vlMGP).getUserTotalLocked(account))`, run the attacker's `registerCode(bytes32 _code)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
