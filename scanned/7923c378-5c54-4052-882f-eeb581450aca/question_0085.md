# Q0085: MGPRelease.claim - claim proceeds when claimable is zero

## Question
Note that in rewards/MGPRelease.sol, claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Can an attacker holding only tokens bought on market reach it via `claim()` under block.timestamp is below startTimestamp and the initial tranche has already been claimed and force `vested` apart from `beneficiaries[account].totalAlloced - initialUnlockedAmount`, breaking the invariant that a claim that moves no value must revert rather than emit for High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim proceeds when claimable is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Precondition: block.timestamp is below startTimestamp and the initial tranche has already been claimed.
- Invariant to test: a claim that moves no value must revert rather than emit; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that block.timestamp is below startTimestamp and the initial tranche has already been claimed, fuzz the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated), and assert after every call that a claim that moves no value must revert rather than emit.
