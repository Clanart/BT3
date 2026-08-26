# Q0673: ArbitrumMWomAirdrop.claim - vested minus claimed can underflow and brick the claim

## Question
rewards/ArbitrumMWomAirdrop.sol: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and block.timestamp is one second after an interval boundary, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `vestingPeriodCount and intervals` and `the elapsed period count` no longer reconcile, violating the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement and realising Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under block.timestamp is one second after an interval boundary, then assert `vestingPeriodCount and intervals` and `the elapsed period count` end identical in both runs.
