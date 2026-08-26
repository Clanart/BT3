# Q2487: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
Note that in rewards/ReferralStorage.sol, registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Can an attacker holding only tokens bought on market reach it via `registerCode(bytes32 _code)` under the attacker cancels a cooldown so their real lock rises with no factor refresh and force `codeOwners[_code]` apart from `userInfos[account].myCode`, breaking the invariant that each account must own at most one code and every code must resolve consistently in both directions for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker cancels a cooldown so their real lock rises with no factor refresh, snapshot `codeOwners[_code]` and `userInfos[account].myCode`, run the attacker's `registerCode(bytes32 _code)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
