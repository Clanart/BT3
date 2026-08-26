# Q1821: Airdrop.claim - allocation is zeroed even when the claim is only partial in value terms

## Question
In rewards/Airdrop.sol, claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Starting from a state where the attacker's allocation is the largest remaining one, can an unprivileged EOA use `claim()` to leave `totalEndRemainingAllocation` inconsistent with `totalRemainingAllocation`, violating the invariant that a claim must not silently forfeit the unvested remainder without an explicit opt-in and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: allocation is zeroed even when the claim is only partial in value terms)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: a claim must not silently forfeit the unvested remainder without an explicit opt-in; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under the attacker's allocation is the largest remaining one, asserting at the end that `totalEndRemainingAllocation` still equals `totalRemainingAllocation` and the PoC's balance delta is non-positive.
