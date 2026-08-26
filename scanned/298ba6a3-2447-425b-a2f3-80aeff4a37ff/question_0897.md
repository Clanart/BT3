# Q0897: DSMath.wmul - the factor is recomputed from a live balance rather than accumulated

## Question
In libraries/DSMath.sol, because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Starting from a state where the attacker raises and lowers their lock repeatedly across blocks, can an unprivileged EOA use `wmul(uint256 x, uint256 y)` to leave `DSMath.sqrt(lockedAmount)` inconsistent with `userInfos[account].factor in ReferralStorage`, violating the invariant that a share of a shared budget must be earned over time, not rewritten by the current balance and extracting High - Theft of unclaimed yield?

## Target
- File/function: libraries/DSMath.sol -> `wmul(uint256 x, uint256 y)` (mechanism: the factor is recomputed from a live balance rather than accumulated)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `wmul(uint256 x, uint256 y)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the operand magnitudes on the reward and conversion paths, chosen by sizing deposits and claims
- Exploit idea: because the factor is a pure function of the current locked amount, every change to that amount rewrites the participant's share of the shared BoostPoint retroactively rather than prospectively. Precondition: the attacker raises and lowers their lock repeatedly across blocks.
- Invariant to test: a share of a shared budget must be earned over time, not rewritten by the current balance; concretely, `DSMath.sqrt(lockedAmount)` must stay reconciled with `userInfos[account].factor in ReferralStorage`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker raises and lowers their lock repeatedly across blocks, have the attacker run `wmul(uint256 x, uint256 y)`, then assert the victim's claimable value and the `DSMath.sqrt(lockedAmount)` versus `userInfos[account].factor in ReferralStorage` relation are unchanged by the attacker's transaction.
