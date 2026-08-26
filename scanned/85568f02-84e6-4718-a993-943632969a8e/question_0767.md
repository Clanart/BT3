# Q0767: MGPRelease.claim - claimable is never bounded by the contract balance

## Question
rewards/MGPRelease.sol: claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. With the exact block at which the linear release is evaluated, and how often it is repeated under attacker control and the beneficiary claims repeatedly inside one block, can an unprivileged caller sequence `claim()` so that `initialUnlockedAmount` and `beneficiaries[account].claimed` no longer reconcile, violating the invariant that the sum of all claimable amounts must never exceed the tokens actually held and realising Critical - Protocol insolvency?

## Target
- File/function: rewards/MGPRelease.sol -> `claim()` (mechanism: claimable is never bounded by the contract balance)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the linear release is evaluated, and how often it is repeated
- Exploit idea: claim() transfers the computed figure with no comparison against the balance held, so once the sum of allocations exceeds the funded balance the later beneficiaries simply revert. Precondition: the beneficiary claims repeatedly inside one block.
- Invariant to test: the sum of all claimable amounts must never exceed the tokens actually held; concretely, `initialUnlockedAmount` must stay reconciled with `beneficiaries[account].claimed`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that the beneficiary claims repeatedly inside one block, fuzz the attacker inputs (the exact block at which the linear release is evaluated, and how often it is repeated), and assert after every call that the sum of all claimable amounts must never exceed the tokens actually held.
