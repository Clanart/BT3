# Q0919: Airdrop.claim - allocation is zeroed even when the claim is only partial in value terms

## Question
In rewards/Airdrop.sol, claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `getClaimableAmount(user)` diverges from `allocations[user]`, the invariant that a claim must not silently forfeit the unvested remainder without an explicit opt-in is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: allocation is zeroed even when the claim is only partial in value terms)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() sets allocations[msg.sender] = 0 and treats the difference as forfeited, so a participant who claims at a moment when the vesting periods have not all elapsed permanently loses the rest. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: a claim must not silently forfeit the unvested remainder without an explicit opt-in; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that exactly one unclaimed allocation remains besides the attacker's, fuzz the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation), and assert after every call that a claim must not silently forfeit the unvested remainder without an explicit opt-in.
