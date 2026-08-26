# Q2212: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
In rewards/Airdrop.sol, each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Does `claim()` let an unprivileged caller exploit that under the token balance held by the contract is below the sum of remaining claimable amounts, so that `getClaimableAmount(user)` diverges from `allocations[user]`, the invariant that a participant must not be able to shrink the denominator that scales their own bonus is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: the token balance held by the contract is below the sum of remaining claimable amounts.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the token balance held by the contract is below the sum of remaining claimable amounts, snapshot `getClaimableAmount(user)` and `allocations[user]`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
