# Q2074: Airdrop.claim - allocation is zeroed even when the claim is only partial in value terms

## Question
In rewards/Airdrop.sol, claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Can an unprivileged attacker reach this through `claim()` while the first honest claim transaction is pending in the mempool, and drive `totalBonus` out of agreement with `aidropToken.balanceOf(address(this))` - breaking the invariant that a claim must not silently forfeit the unvested remainder without an explicit opt-in - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: allocation is zeroed even when the claim is only partial in value terms)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: a claim must not silently forfeit the unvested remainder without an explicit opt-in; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation) under the first honest claim transaction is pending in the mempool, asserting on every row that a claim must not silently forfeit the unvested remainder without an explicit opt-in.
