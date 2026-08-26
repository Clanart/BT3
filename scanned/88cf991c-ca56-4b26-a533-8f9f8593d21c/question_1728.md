# Q1728: ArbitrumMWomAirdrop.claim - the destination is chosen by the claimant

## Question
rewards/ArbitrumMWomAirdrop.sol: the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Under the contract's reward balance is below the sum of unclaimed entitlements, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `vestingPeriodCount and intervals` unreconciled with `the elapsed period count`, violates the invariant that the settlement form of a vested claim must be fixed by the grant, not chosen per claim, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: the destination is chosen by the claimant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: the settlement form of a vested claim must be fixed by the grant, not chosen per claim; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the contract's reward balance is below the sum of unclaimed entitlements, asserting at the end that `vestingPeriodCount and intervals` still equals `the elapsed period count` and the PoC's balance delta is non-positive.
