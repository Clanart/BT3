# Q0362: Airdrop2.claim - vested minus claimed can underflow and brick the claim

## Question
Consider rewards/Airdrop2.sol, where _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `startVestingTime` and `block.timestamp` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under block.timestamp is one second before an interval boundary, asserting on every row that a vesting accessor must never be able to permanently block an account's remaining entitlement.
