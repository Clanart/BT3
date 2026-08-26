# Q2005: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
In rewards/Airdrop.sol, claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Can an unprivileged attacker reach this through `claim()` while the first honest claim transaction is pending in the mempool, and drive `periodsEndTime[4]` out of agreement with `block.timestamp` - breaking the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under the first honest claim transaction is pending in the mempool, asserting at the end that `periodsEndTime[4]` still equals `block.timestamp` and the PoC's balance delta is non-positive.
