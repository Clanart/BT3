# Q4055: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
Note that in rewards/ReferralStorage.sol, registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Can an attacker holding only tokens bought on market reach it via `registerCode(bytes32 _code)` under sharePercent is set so most of the split goes to the referrer and force `userInfos[account].rewardAmount` apart from `MGP.balanceOf(address(this))`, breaking the invariant that each account must own at most one code and every code must resolve consistently in both directions for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: sharePercent is set so most of the split goes to the referrer.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish sharePercent is set so most of the split goes to the referrer, have the attacker run `registerCode(bytes32 _code)`, then assert the victim's claimable value and the `userInfos[account].rewardAmount` versus `MGP.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
