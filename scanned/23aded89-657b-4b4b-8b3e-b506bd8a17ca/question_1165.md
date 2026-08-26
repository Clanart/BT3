# Q1165: Airdrop.claim - claim only auto-snapshots when the value is still zero

## Question
In rewards/Airdrop.sol, claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Can an unprivileged attacker reach this through `claim()` while totalBonus has grown large from earlier forfeits, and drive `totalBonus` out of agreement with `aidropToken.balanceOf(address(this))` - breaking the invariant that the automatic and the manual path to a one-shot snapshot must enforce the same constraint - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim only auto-snapshots when the value is still zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() calls updateEndRemainingAllocation only under if (totalEndRemainingAllocation == 0), so the automatic path is one-shot while the public path is not, and the two disagree about how many times the snapshot may be taken. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: the automatic and the manual path to a one-shot snapshot must enforce the same constraint; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalBonus has grown large from earlier forfeits, call `claim()`, and assert `totalBonus` equals `aidropToken.balanceOf(address(this))` and that no account can withdraw more than it put in.
