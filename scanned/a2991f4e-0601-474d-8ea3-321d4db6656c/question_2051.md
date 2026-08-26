# Q2051: Airdrop.claim - claim reverts once the balance runs short

## Question
In rewards/Airdrop.sol, claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Can an unprivileged attacker reach this through `claim()` while the first honest claim transaction is pending in the mempool, and drive `totalEndRemainingAllocation` out of agreement with `totalRemainingAllocation` - breaking the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held - for Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the first honest claim transaction is pending in the mempool, then assert `totalEndRemainingAllocation` and `totalRemainingAllocation` end identical in both runs.
