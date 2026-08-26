# Q1548: Airdrop.claim - allocation is zeroed even when the claim is only partial in value terms

## Question
Consider rewards/Airdrop.sol, where claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Assuming the attacker's allocation is small relative to the original totalRemainingAllocation, can an unprivileged attacker turn this into a divergence between `sum of all allocations` and `aidropToken.balanceOf(address(this))` via `claim()`, breaking the invariant that a claim must not silently forfeit the unvested remainder without an explicit opt-in and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: allocation is zeroed even when the claim is only partial in value terms)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: a claim must not silently forfeit the unvested remainder without an explicit opt-in; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker's allocation is small relative to the original totalRemainingAllocation, then assert `sum of all allocations` and `aidropToken.balanceOf(address(this))` end identical in both runs.
