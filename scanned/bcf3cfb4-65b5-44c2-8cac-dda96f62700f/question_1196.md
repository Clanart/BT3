# Q1196: Airdrop2.claim - the destination is chosen by the claimant

## Question
In rewards/Airdrop2.sol, the isLock flag routes the payout to vlmgp.lockFor when isLock is true and a plain safeTransfer otherwise, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Starting from a state where the elapsed period count has already exceeded vestingPeriodCount, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `vested computed in _getClaimable` inconsistent with `claimedAmount[account]`, violating the invariant that the settlement form of a vested claim must be fixed by the grant, not chosen per claim and extracting High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: the destination is chosen by the claimant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock flag routes the payout to vlmgp.lockFor when isLock is true and a plain safeTransfer otherwise, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: the settlement form of a vested claim must be fixed by the grant, not chosen per claim; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that the elapsed period count has already exceeded vestingPeriodCount, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that the settlement form of a vested claim must be fixed by the grant, not chosen per claim.
