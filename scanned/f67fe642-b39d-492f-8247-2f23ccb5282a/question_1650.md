# Q1650: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
rewards/ReferralStorage.sol: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. With any unclaimed 32-byte code, and how many times registration is repeated under attacker control and the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, can an unprivileged caller sequence `registerCode(bytes32 _code)` so that `refererPercentage + refereePercentage` and `DENOMINATOR` no longer reconcile, violating the invariant that each account must own at most one code and every code must resolve consistently in both directions and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: the contract's MGP balance is smaller than the sum of all accrued rewardAmount values.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `refererPercentage + refereePercentage` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's MGP balance is smaller than the sum of all accrued rewardAmount values, call `registerCode(bytes32 _code)`, and assert `refererPercentage + refereePercentage` equals `DENOMINATOR` and that no account can withdraw more than it put in.
