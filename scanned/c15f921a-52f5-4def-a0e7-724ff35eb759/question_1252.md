# Q1252: Airdrop.claim - allocation is zeroed even when the claim is only partial in value terms

## Question
Consider rewards/Airdrop.sol, where claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Assuming totalBonus has grown large from earlier forfeits, can an unprivileged attacker turn this into a divergence between `periodsEndTime[4]` and `block.timestamp` via `claim()`, breaking the invariant that a claim must not silently forfeit the unvested remainder without an explicit opt-in and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: allocation is zeroed even when the claim is only partial in value terms)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: a claim must not silently forfeit the unvested remainder without an explicit opt-in; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up totalBonus has grown large from earlier forfeits, snapshot `periodsEndTime[4]` and `block.timestamp`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
