# Q1752: ArbitrumMWomAirdrop.claim - startVestingTime is the only gate and is compared twice

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the contract's reward balance is below the sum of unclaimed entitlements, and drive `claimable` out of agreement with `reward.balanceOf(address(this))` - breaking the invariant that a vesting origin must not be able to move under accounts that have already claimed against it - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's reward balance is below the sum of unclaimed entitlements, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `claimable` equals `reward.balanceOf(address(this))` and that no account can withdraw more than it put in.
