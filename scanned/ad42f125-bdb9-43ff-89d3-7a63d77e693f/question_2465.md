# Q2465: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
Consider rewards/Airdrop.sol, where each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Assuming the attacker calls updateEndRemainingAllocation and claim in the same transaction, can an unprivileged attacker turn this into a divergence between `periodsEndTime[4]` and `block.timestamp` via `claim()`, breaking the invariant that a participant must not be able to shrink the denominator that scales their own bonus and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: the attacker calls updateEndRemainingAllocation and claim in the same transaction.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that the attacker calls updateEndRemainingAllocation and claim in the same transaction, fuzz the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation), and assert after every call that a participant must not be able to shrink the denominator that scales their own bonus.
