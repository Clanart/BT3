# Q2557: Airdrop.claim - claim reverts once the balance runs short

## Question
Consider rewards/Airdrop.sol, where claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Assuming the attacker calls updateEndRemainingAllocation and claim in the same transaction, can an unprivileged attacker turn this into a divergence between `getBonusAmount(user)` and `allocations[user]` via `claim()`, breaking the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held and producing Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: the attacker calls updateEndRemainingAllocation and claim in the same transaction.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls updateEndRemainingAllocation and claim in the same transaction, snapshot `getBonusAmount(user)` and `allocations[user]`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
