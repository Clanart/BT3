# Q3180: ReferralStorage.registerCode - old code keeps pointing at the account after re-registration

## Question
In rewards/ReferralStorage.sol, registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Starting from a state where the referee has a large pending MGP claim in MasterMagpie, can an unprivileged EOA use `registerCode(bytes32 _code)` to leave `BoostPoint` inconsistent with `totalBoostFactor`, violating the invariant that each account must own at most one code and every code must resolve consistently in both directions and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: old code keeps pointing at the account after re-registration)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() never clears codeOwners for the account's previous code, so two distinct codes can resolve to the same owner while only one is reflected in userInfos[account].myCode. Precondition: the referee has a large pending MGP claim in MasterMagpie.
- Invariant to test: each account must own at most one code and every code must resolve consistently in both directions; concretely, `BoostPoint` must stay reconciled with `totalBoostFactor`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `registerCode(bytes32 _code)`: constrain the setup so that the referee has a large pending MGP claim in MasterMagpie, fuzz the attacker inputs (any unclaimed 32-byte code, and how many times registration is repeated), and assert after every call that each account must own at most one code and every code must resolve consistently in both directions.
