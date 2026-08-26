# Q0672: Airdrop2.claim - vested minus claimed can underflow and brick the claim

## Question
In rewards/Airdrop2.sol, _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while block.timestamp is one second after an interval boundary, and drive `vestingPeriodCount and intervals` out of agreement with `the elapsed period count` - breaking the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: block.timestamp is one second after an interval boundary.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that block.timestamp is one second after an interval boundary, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that a vesting accessor must never be able to permanently block an account's remaining entitlement.
