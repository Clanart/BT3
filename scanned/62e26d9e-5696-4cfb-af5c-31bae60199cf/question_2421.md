# Q2421: ArbitrumMWomAirdrop.claim - the destination is chosen by the claimant

## Question
rewards/ArbitrumMWomAirdrop.sol - the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the claim is placed in the same block as another large claim, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `vested computed in _getClaimable` and `claimedAmount[account]` and the invariant that the settlement form of a vested claim must be fixed by the grant, not chosen per claim, yielding High - Theft of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: the destination is chosen by the claimant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock flag routes the payout to the configured locker and helper destinations, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Precondition: the claim is placed in the same block as another large claim.
- Invariant to test: the settlement form of a vested claim must be fixed by the grant, not chosen per claim; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the claim is placed in the same block as another large claim, snapshot `vested computed in _getClaimable` and `claimedAmount[account]`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
