# Q2142: ReferralStorage.useCode - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Starting from a state where the attacker locked vlMGP before registering a code, can an unprivileged EOA use `useCode(bytes32 _code)` to leave `userInfos[account].rewardAmount` inconsistent with `MGP.balanceOf(address(this))`, violating the invariant that a boost weight must not reward splitting one position across addresses and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `useCode(bytes32 _code)` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `useCode(bytes32 _code)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: which code is bound, and from which of the attacker's own addresses
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker locked vlMGP before registering a code.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `useCode(bytes32 _code)`: constrain the setup so that the attacker locked vlMGP before registering a code, fuzz the attacker inputs (which code is bound, and from which of the attacker's own addresses), and assert after every call that a boost weight must not reward splitting one position across addresses.
