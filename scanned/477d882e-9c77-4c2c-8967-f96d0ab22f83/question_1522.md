# Q1522: Airdrop.claim - claim reverts once the balance runs short

## Question
rewards/Airdrop.sol: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Under the attacker's allocation is small relative to the original totalRemainingAllocation, is there an unprivileged sequence of `claim()` that leaves `periodsEndTime[4]` unreconciled with `block.timestamp`, violates the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held, and delivers Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under the attacker's allocation is small relative to the original totalRemainingAllocation, asserting at the end that `periodsEndTime[4]` still equals `block.timestamp` and the PoC's balance delta is non-positive.
