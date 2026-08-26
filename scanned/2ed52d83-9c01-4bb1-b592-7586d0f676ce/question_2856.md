# Q2856: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
rewards/ReferralStorage.sol: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Under the attacker splits one large lock across many addresses that each register a code, is there an unprivileged sequence of `registerCode(bytes32 _code)` that leaves `myReferer[account]` unreconciled with `userInfos[account].codeIUsed`, violates the invariant that each account must own at most one code and every code must resolve consistently in both directions, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `myReferer[account]` must stay reconciled with `userInfos[account].codeIUsed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (any unclaimed 32-byte code, and how many times registration is repeated) under the attacker splits one large lock across many addresses that each register a code, asserting on every row that each account must own at most one code and every code must resolve consistently in both directions.
