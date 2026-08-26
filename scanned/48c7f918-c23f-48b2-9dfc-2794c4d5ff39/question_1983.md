# Q1983: Airdrop2.claim - startVestingTime is the only gate and is compared twice

## Question
rewards/Airdrop2.sol: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Under the claimant sets isLock to true so the vlMGP lock leg runs, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `claimedAmount[account]` unreconciled with `totalAmount proven by the merkle leaf`, violates the invariant that a vesting origin must not be able to move under accounts that have already claimed against it, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that the claimant sets isLock to true so the vlMGP lock leg runs, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that a vesting origin must not be able to move under accounts that have already claimed against it.
