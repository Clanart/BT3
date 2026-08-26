# Q0084: ArbitrumMWomAirdrop.claim - period count truncates so a whole interval can be lost

## Question
In rewards/ArbitrumMWomAirdrop.sol, vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the account appears in the merkle tree under two different totalAmount values, so that `startVestingTime` diverges from `block.timestamp`, the invariant that a vesting curve must not permanently penalise a claim placed near a period boundary is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: period count truncates so a whole interval can be lost)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Precondition: the account appears in the merkle tree under two different totalAmount values.
- Invariant to test: a vesting curve must not permanently penalise a claim placed near a period boundary; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the account appears in the merkle tree under two different totalAmount values, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `startVestingTime` equals `block.timestamp` and that no account can withdraw more than it put in.
