# Q0610: Airdrop2.claim - startVestingTime is the only gate and is compared twice

## Question
Consider rewards/Airdrop2.sol, where claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `claimedAmount[account]` and `totalAmount proven by the merkle leaf` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a vesting origin must not be able to move under accounts that have already claimed against it and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under block.timestamp is one second before an interval boundary, then assert `claimedAmount[account]` and `totalAmount proven by the merkle leaf` end identical in both runs.
