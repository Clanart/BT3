# Q0578: Airdrop.claim - allocation is zeroed even when the claim is only partial in value terms

## Question
In rewards/Airdrop.sol, claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Can an unprivileged attacker reach this through `claim()` while most participants have already claimed so totalRemainingAllocation is small, and drive `getBonusAmount(user)` out of agreement with `allocations[user]` - breaking the invariant that a claim must not silently forfeit the unvested remainder without an explicit opt-in - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: allocation is zeroed even when the claim is only partial in value terms)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: a claim must not silently forfeit the unvested remainder without an explicit opt-in; concretely, `getBonusAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under most participants have already claimed so totalRemainingAllocation is small, then assert `getBonusAmount(user)` and `allocations[user]` end identical in both runs.
