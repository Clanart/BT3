# Q3486: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
In rewards/ReferralStorage.sol, registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Does `registerCode(bytes32 _code)` let an unprivileged caller exploit that under the attacker calls multiclaimFor on a set of referred accounts in one block, so that `userInfos[account].factor` diverges from `totalBoostFactor`, the invariant that each account must own at most one code and every code must resolve consistently in both directions is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `userInfos[account].factor` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls multiclaimFor on a set of referred accounts in one block, call `registerCode(bytes32 _code)`, and assert `userInfos[account].factor` equals `totalBoostFactor` and that no account can withdraw more than it put in.
