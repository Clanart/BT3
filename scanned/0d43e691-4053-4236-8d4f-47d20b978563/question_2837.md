# Q2837: ReferralStorage.registerCode - re-registering a second code resets the tier to one

## Question
In rewards/ReferralStorage.sol, registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Starting from a state where the attacker splits one large lock across many addresses that each register a code, can an unprivileged EOA use `registerCode(bytes32 _code)` to leave `codeOwners[_code]` inconsistent with `userInfos[account].myCode`, violating the invariant that the code-ownership map and the per-user record must never disagree about which code an account owns and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: re-registering a second code resets the tier to one)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: registerCode() unconditionally writes userInfos[msg.sender].tier = 1 and overwrites myCode while codeOwners still maps the previous code to the same account, so the code-ownership map and the per-user record diverge. Precondition: the attacker splits one large lock across many addresses that each register a code.
- Invariant to test: the code-ownership map and the per-user record must never disagree about which code an account owns; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker splits one large lock across many addresses that each register a code, snapshot `codeOwners[_code]` and `userInfos[account].myCode`, run the attacker's `registerCode(bytes32 _code)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
