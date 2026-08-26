# Q1014: ArbitrumMWomAirdrop.claim - period count truncates so a whole interval can be lost

## Question
rewards/ArbitrumMWomAirdrop.sol: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Under the elapsed period count has already exceeded vestingPeriodCount, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `claimedAmount[account]` unreconciled with `totalAmount proven by the merkle leaf`, violates the invariant that a vesting curve must not permanently penalise a claim placed near a period boundary, and delivers High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: period count truncates so a whole interval can be lost)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: a vesting curve must not permanently penalise a claim placed near a period boundary; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the elapsed period count has already exceeded vestingPeriodCount, then assert `claimedAmount[account]` and `totalAmount proven by the merkle leaf` end identical in both runs.
