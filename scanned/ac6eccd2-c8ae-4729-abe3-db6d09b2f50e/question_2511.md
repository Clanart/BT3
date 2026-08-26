# Q2511: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
Consider rewards/Airdrop.sol, where claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Assuming the attacker calls updateEndRemainingAllocation and claim in the same transaction, can an unprivileged attacker turn this into a divergence between `totalEndRemainingAllocation` and `totalRemainingAllocation` via `claim()`, breaking the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: the attacker calls updateEndRemainingAllocation and claim in the same transaction.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls updateEndRemainingAllocation and claim in the same transaction, call `claim()`, and assert `totalEndRemainingAllocation` equals `totalRemainingAllocation` and that no account can withdraw more than it put in.
