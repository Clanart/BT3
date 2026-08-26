# Q2214: ArbitrumMWomAirdrop.claim - startVestingTime is the only gate and is compared twice

## Question
Note that in rewards/ArbitrumMWomAirdrop.sol, claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the claimant sets isLock to false so the plain transfer leg runs and force `vested computed in _getClaimable` apart from `claimedAmount[account]`, breaking the invariant that a vesting origin must not be able to move under accounts that have already claimed against it for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: the claimant sets isLock to false so the plain transfer leg runs.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the claimant sets isLock to false so the plain transfer leg runs, then assert `vested computed in _getClaimable` and `claimedAmount[account]` end identical in both runs.
