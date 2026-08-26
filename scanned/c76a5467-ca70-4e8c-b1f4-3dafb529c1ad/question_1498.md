# Q1498: ArbitrumMWomAirdrop.claim - startVestingTime is the only gate and is compared twice

## Question
Consider rewards/ArbitrumMWomAirdrop.sol, where claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Assuming the account has already claimed the initial five percent tranche, can an unprivileged attacker turn this into a divergence between `vestingPeriodCount and intervals` and `the elapsed period count` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a vesting origin must not be able to move under accounts that have already claimed against it and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: startVestingTime is the only gate and is compared twice)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() requires block.timestamp >= startVestingTime and _getClaimable independently returns zero below it, so the two checks must agree, and any change to startVestingTime retroactively rewrites every account's vested figure against their existing claimed counter. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: a vesting origin must not be able to move under accounts that have already claimed against it; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the account has already claimed the initial five percent tranche, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `vestingPeriodCount and intervals` versus `the elapsed period count` relation are unchanged by the attacker's transaction.
