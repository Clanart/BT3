# Q2464: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
Note that in rewards/ReferralStorage.sol, registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Can an attacker holding only tokens bought on market reach it via `registerCode(bytes32 _code)` under the attacker cancels a cooldown so their real lock rises with no factor refresh and force `tiers[tierId].rewardPercentage + _calBoosted(referer)` apart from `DENOMINATOR`, breaking the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: the attacker cancels a cooldown so their real lock rises with no factor refresh.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `tiers[tierId].rewardPercentage + _calBoosted(referer)` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `registerCode(bytes32 _code)` sequence atomically under the attacker cancels a cooldown so their real lock rises with no factor refresh, asserting at the end that `tiers[tierId].rewardPercentage + _calBoosted(referer)` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
