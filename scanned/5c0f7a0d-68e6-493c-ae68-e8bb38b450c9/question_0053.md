# Q0053: ArbitrumMWomAirdrop.claim - vested minus claimed can underflow and brick the claim

## Question
In rewards/ArbitrumMWomAirdrop.sol, _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the account appears in the merkle tree under two different totalAmount values, so that `vested computed in _getClaimable` diverges from `claimedAmount[account]`, the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: the account appears in the merkle tree under two different totalAmount values.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the account appears in the merkle tree under two different totalAmount values, asserting at the end that `vested computed in _getClaimable` still equals `claimedAmount[account]` and the PoC's balance delta is non-positive.
