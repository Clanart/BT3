# Q1549: Airdrop2.claim - vested minus claimed can underflow and brick the claim

## Question
In rewards/Airdrop2.sol, _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the contract's reward balance is below the sum of unclaimed entitlements, so that `vested computed in _getClaimable` diverges from `claimedAmount[account]`, the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the contract's reward balance is below the sum of unclaimed entitlements, snapshot `vested computed in _getClaimable` and `claimedAmount[account]`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
