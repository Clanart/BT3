# Q1417: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
Consider rewards/Airdrop.sol, where each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Assuming the attacker's allocation is small relative to the original totalRemainingAllocation, can an unprivileged attacker turn this into a divergence between `totalEndRemainingAllocation` and `totalRemainingAllocation` via `claim()`, breaking the invariant that a participant must not be able to shrink the denominator that scales their own bonus and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's allocation is small relative to the original totalRemainingAllocation, snapshot `totalEndRemainingAllocation` and `totalRemainingAllocation`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
