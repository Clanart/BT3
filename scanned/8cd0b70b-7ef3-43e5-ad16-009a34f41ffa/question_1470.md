# Q1470: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
rewards/Airdrop.sol: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. With the ordering of the claim against every other claimant and against updateEndRemainingAllocation under attacker control and the attacker's allocation is small relative to the original totalRemainingAllocation, can an unprivileged caller sequence `claim()` so that `getBonusAmount(user)` and `allocations[user]` no longer reconcile, violating the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation) under the attacker's allocation is small relative to the original totalRemainingAllocation, asserting on every row that the automatic and the manual path to a one-shot snapshot must enforce the same constraint.
