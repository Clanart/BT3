# Q2283: ArbitrumMWomAirdrop.claim - period count truncates so a whole interval can be lost

## Question
rewards/ArbitrumMWomAirdrop.sol - vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the claim is placed in the same block as another large claim, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `claimedAmount[account]` and `totalAmount proven by the merkle leaf` and the invariant that a vesting curve must not permanently penalise a claim placed near a period boundary, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: period count truncates so a whole interval can be lost)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Precondition: the claim is placed in the same block as another large claim.
- Invariant to test: a vesting curve must not permanently penalise a claim placed near a period boundary; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the claim is placed in the same block as another large claim, then assert `claimedAmount[account]` and `totalAmount proven by the merkle leaf` end identical in both runs.
