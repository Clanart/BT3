# Q1225: Airdrop2.claim - startVestingTime is the only gate and is compared twice

## Question
rewards/Airdrop2.sol: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and the elapsed period count has already exceeded vestingPeriodCount, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `startVestingTime` and `block.timestamp` no longer reconcile, violating the invariant that a vesting origin must not be able to move under accounts that have already claimed against it and realising Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the elapsed period count has already exceeded vestingPeriodCount, asserting at the end that `startVestingTime` still equals `block.timestamp` and the PoC's balance delta is non-positive.
