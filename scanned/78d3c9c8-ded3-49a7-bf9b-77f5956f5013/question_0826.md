# Q0826: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
In rewards/Airdrop.sol, claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `totalEndRemainingAllocation` diverges from `totalRemainingAllocation`, the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under exactly one unclaimed allocation remains besides the attacker's, asserting at the end that `totalEndRemainingAllocation` still equals `totalRemainingAllocation` and the PoC's balance delta is non-positive.
