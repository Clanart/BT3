# Q2073: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
In rewards/ReferralStorage.sol, registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Can an unprivileged attacker reach this through `registerCode(bytes32 _code)` while the attacker locked vlMGP before registering a code, and drive `tiers[tierId].rewardPercentage + _calBoosted(referer)` out of agreement with `DENOMINATOR` - breaking the invariant that each account must own at most one code and every code must resolve consistently in both directions - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locked vlMGP before registering a code, have the attacker run `registerCode(bytes32 _code)`, then assert the victim's claimable value and the `tiers[tierId].rewardPercentage + _calBoosted(referer)` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
