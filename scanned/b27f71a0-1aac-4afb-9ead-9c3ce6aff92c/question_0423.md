# Q0423: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
In rewards/Airdrop.sol, each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Can an unprivileged attacker reach this through `claim()` while most participants have already claimed so totalRemainingAllocation is small, and drive `getClaimableAmount(user)` out of agreement with `allocations[user]` - breaking the invariant that a participant must not be able to shrink the denominator that scales their own bonus - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under most participants have already claimed so totalRemainingAllocation is small, asserting at the end that `getClaimableAmount(user)` still equals `allocations[user]` and the PoC's balance delta is non-positive.
