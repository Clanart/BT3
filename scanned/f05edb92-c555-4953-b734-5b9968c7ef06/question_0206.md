# Q0206: Airdrop.claim - claim reverts once the balance runs short

## Question
In rewards/Airdrop.sol, claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `claim()` to leave `totalEndRemainingAllocation` inconsistent with `totalRemainingAllocation`, violating the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, snapshot `totalEndRemainingAllocation` and `totalRemainingAllocation`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
