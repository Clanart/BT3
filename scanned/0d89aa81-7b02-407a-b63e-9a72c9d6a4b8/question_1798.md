# Q1798: Airdrop.claim - claim reverts once the balance runs short

## Question
In rewards/Airdrop.sol, claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Starting from a state where the attacker's allocation is the largest remaining one, can an unprivileged EOA use `claim()` to leave `sum of all allocations` inconsistent with `aidropToken.balanceOf(address(this))`, violating the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held and extracting Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker's allocation is the largest remaining one, call `claim()`, and assert `sum of all allocations` equals `aidropToken.balanceOf(address(this))` and that no account can withdraw more than it put in.
