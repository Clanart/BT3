# Q1472: ArbitrumMWomAirdrop.claim - the destination is chosen by the claimant

## Question
rewards/ArbitrumMWomAirdrop.sol: the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Under the account has already claimed the initial five percent tranche, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `startVestingTime` unreconciled with `block.timestamp`, violates the invariant that the settlement form of a vested claim must be fixed by the grant, not chosen per claim, and delivers High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: the destination is chosen by the claimant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: the settlement form of a vested claim must be fixed by the grant, not chosen per claim; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the account has already claimed the initial five percent tranche, asserting on every row that the settlement form of a vested claim must be fixed by the grant, not chosen per claim.
