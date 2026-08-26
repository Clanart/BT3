# Q0144: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
In rewards/Airdrop.sol, claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `claim()` to leave `periodsEndTime[4]` inconsistent with `block.timestamp`, violating the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation) under block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, asserting on every row that the automatic and the manual path to a one-shot snapshot must enforce the same constraint.
