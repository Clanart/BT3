# Q0611: ArbitrumMWomAirdrop.claim - startVestingTime is the only gate and is compared twice

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Starting from a state where block.timestamp is one second before an interval boundary, can an unprivileged EOA use `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` to leave `claimedAmount[account]` inconsistent with `totalAmount proven by the merkle leaf`, violating the invariant that a vesting origin must not be able to move under accounts that have already claimed against it and extracting Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under block.timestamp is one second before an interval boundary, asserting at the end that `claimedAmount[account]` still equals `totalAmount proven by the merkle leaf` and the PoC's balance delta is non-positive.
