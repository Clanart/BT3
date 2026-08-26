# Q0485: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
In rewards/Airdrop.sol, claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Can an unprivileged attacker reach this through `claim()` while most participants have already claimed so totalRemainingAllocation is small, and drive `sum of all allocations` out of agreement with `aidropToken.balanceOf(address(this))` - breaking the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish most participants have already claimed so totalRemainingAllocation is small, have the attacker run `claim()`, then assert the victim's claimable value and the `sum of all allocations` versus `aidropToken.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
