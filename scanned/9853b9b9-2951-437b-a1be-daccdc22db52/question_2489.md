# Q2489: Airdrop2.claim - vested minus claimed can underflow and brick the claim

## Question
rewards/Airdrop2.sol: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and the computed claimable is exactly zero, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `claimedAmount[account]` and `totalAmount proven by the merkle leaf` no longer reconcile, violating the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement and realising Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: the computed claimable is exactly zero.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing) under the computed claimable is exactly zero, asserting on every row that a vesting accessor must never be able to permanently block an account's remaining entitlement.
