# Q0888: Airdrop.claim - claim reverts once the balance runs short

## Question
In rewards/Airdrop.sol, claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `getBonusAmount(user)` diverges from `allocations[user]`, the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish exactly one unclaimed allocation remains besides the attacker's, have the attacker run `claim()`, then assert the victim's claimable value and the `getBonusAmount(user)` versus `allocations[user]` relation are unchanged by the attacker's transaction.
