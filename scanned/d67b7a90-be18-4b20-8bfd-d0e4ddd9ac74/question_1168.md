# Q1168: MGPRelease.claim - claim proceeds when claimable is zero

## Question
rewards/MGPRelease.sol: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. With the exact block at which the linear release is evaluated, and how often it is repeated under attacker control and initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, can an unprivileged caller sequence `claim()` so that `vested` and `beneficiaries[account].totalAlloced - initialUnlockedAmount` no longer reconcile, violating the invariant that a claim that moves no value must revert rather than emit and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claim proceeds when claimable is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() has no guard against a zero claimable, so it performs a zero transfer and emits a Claimed event, making a no-op indistinguishable from a real release. Precondition: initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation.
- Invariant to test: a claim that moves no value must revert rather than emit; concretely, `vested` must stay reconciled with `beneficiaries[account].totalAlloced - initialUnlockedAmount`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish initialUnlockPercentage is set so the initial tranche is a large fraction of the allocation, have the attacker run `claim()`, then assert the victim's claimable value and the `vested` versus `beneficiaries[account].totalAlloced - initialUnlockedAmount` relation are unchanged by the attacker's transaction.
