# Q1984: ArbitrumMWomAirdrop.claim - startVestingTime is the only gate and is compared twice

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the claimant sets isLock to true so the vlMGP lock leg runs, and drive `claimedAmount[account]` out of agreement with `totalAmount proven by the merkle leaf` - breaking the invariant that a vesting origin must not be able to move under accounts that have already claimed against it - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the claimant sets isLock to true so the vlMGP lock leg runs, then assert `claimedAmount[account]` and `totalAmount proven by the merkle leaf` end identical in both runs.
