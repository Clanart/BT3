# Q0083: Airdrop2.claim - period count truncates so a whole interval can be lost

## Question
rewards/Airdrop2.sol - vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Can an unprivileged attacker controlling totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing, under the account appears in the merkle tree under two different totalAmount values, exploit this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to break the reconciliation between `startVestingTime` and `block.timestamp` and the invariant that a vesting curve must not permanently penalise a claim placed near a period boundary, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: period count truncates so a whole interval can be lost)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Precondition: the account appears in the merkle tree under two different totalAmount values.
- Invariant to test: a vesting curve must not permanently penalise a claim placed near a period boundary; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the account appears in the merkle tree under two different totalAmount values, snapshot `startVestingTime` and `block.timestamp`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
