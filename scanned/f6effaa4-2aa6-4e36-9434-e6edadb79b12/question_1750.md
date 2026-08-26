# Q1750: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
rewards/Airdrop.sol: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. With the ordering of the claim against every other claimant and against updateEndRemainingAllocation under attacker control and the attacker's allocation is the largest remaining one, can an unprivileged caller sequence `claim()` so that `getClaimableAmount(user)` and `allocations[user]` no longer reconcile, violating the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's allocation is the largest remaining one, snapshot `getClaimableAmount(user)` and `allocations[user]`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
