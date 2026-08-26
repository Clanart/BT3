# Q3503: ReferralStorage.registerCode - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Starting from a state where the attacker calls multiclaimFor on a set of referred accounts in one block, can an unprivileged EOA use `registerCode(bytes32 _code)` to leave `codeOwners[_code]` inconsistent with `userInfos[account].myCode`, violating the invariant that a boost weight must not reward splitting one position across addresses and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `registerCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `registerCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: any unclaimed 32-byte code, and how many times registration is repeated
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker calls multiclaimFor on a set of referred accounts in one block.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `codeOwners[_code]` must stay reconciled with `userInfos[account].myCode`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `registerCode(bytes32 _code)`: constrain the setup so that the attacker calls multiclaimFor on a set of referred accounts in one block, fuzz the attacker inputs (any unclaimed 32-byte code, and how many times registration is repeated), and assert after every call that a boost weight must not reward splitting one position across addresses.
