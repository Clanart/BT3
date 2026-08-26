# Q0921: ArbitrumMWomAirdrop.claim - startVestingTime is the only gate and is compared twice

## Question
rewards/ArbitrumMWomAirdrop.sol: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and block.timestamp is one second after an interval boundary, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `vested computed in _getClaimable` and `claimedAmount[account]` no longer reconcile, violating the invariant that a vesting origin must not be able to move under accounts that have already claimed against it and realising Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up block.timestamp is one second after an interval boundary, snapshot `vested computed in _getClaimable` and `claimedAmount[account]`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
