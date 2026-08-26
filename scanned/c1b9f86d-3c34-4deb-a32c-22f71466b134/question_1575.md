# Q1575: Airdrop2.claim - period count truncates so a whole interval can be lost

## Question
In rewards/Airdrop2.sol, vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the contract's reward balance is below the sum of unclaimed entitlements, and drive `startVestingTime` out of agreement with `block.timestamp` - breaking the invariant that a vesting curve must not permanently penalise a claim placed near a period boundary - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: period count truncates so a whole interval can be lost)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a vesting curve must not permanently penalise a claim placed near a period boundary; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the contract's reward balance is below the sum of unclaimed entitlements, asserting on every row that a vesting curve must not permanently penalise a claim placed near a period boundary.
