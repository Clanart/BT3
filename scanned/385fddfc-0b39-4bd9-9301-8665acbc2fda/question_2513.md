# Q2513: ArbitrumMWomAirdrop.claim - period count truncates so a whole interval can be lost

## Question
Consider rewards/ArbitrumMWomAirdrop.sol, where vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Assuming the computed claimable is exactly zero, can an unprivileged attacker turn this into a divergence between `vested computed in _getClaimable` and `claimedAmount[account]` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a vesting curve must not permanently penalise a claim placed near a period boundary and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: period count truncates so a whole interval can be lost)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Precondition: the computed claimable is exactly zero.
- Invariant to test: a vesting curve must not permanently penalise a claim placed near a period boundary; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed claimable is exactly zero, then assert `vested computed in _getClaimable` and `claimedAmount[account]` end identical in both runs.
