# Q0889: Airdrop2.claim - the destination is chosen by the claimant

## Question
In rewards/Airdrop2.sol, the isLock flag routes the payout to vlmgp.lockFor when isLock is true and a plain safeTransfer otherwise, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while block.timestamp is one second after an interval boundary, and drive `claimedAmount[account]` out of agreement with `totalAmount proven by the merkle leaf` - breaking the invariant that the settlement form of a vested claim must be fixed by the grant, not chosen per claim - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: the destination is chosen by the claimant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: the isLock flag routes the payout to vlmgp.lockFor when isLock is true and a plain safeTransfer otherwise, so the claimant decides whether the value arrives liquid or time-locked and can pick whichever settles better for them at that instant. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: the settlement form of a vested claim must be fixed by the grant, not chosen per claim; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that block.timestamp is one second after an interval boundary, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that the settlement form of a vested claim must be fixed by the grant, not chosen per claim.
