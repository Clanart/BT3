# Q1164: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
In rewards/ReferralStorage.sol, registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Starting from a state where BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, can an unprivileged EOA use `registerCode(bytes32 _code)` to leave `userInfos[account].rewardAmount` inconsistent with `MGP.balanceOf(address(this))`, violating the invariant that each account must own at most one code and every code must resolve consistently in both directions and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `registerCode(bytes32 _code)` sequence atomically under BoostPoint plus the referrer's tier percentage exceeds DENOMINATOR, asserting at the end that `userInfos[account].rewardAmount` still equals `MGP.balanceOf(address(this))` and the PoC's balance delta is non-positive.
