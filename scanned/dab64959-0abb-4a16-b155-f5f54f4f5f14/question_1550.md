# Q1550: ArbitrumMWomAirdrop.claim - vested minus claimed can underflow and brick the claim

## Question
Note that in rewards/ArbitrumMWomAirdrop.sol, _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the contract's reward balance is below the sum of unclaimed entitlements and force `vested computed in _getClaimable` apart from `claimedAmount[account]`, breaking the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's reward balance is below the sum of unclaimed entitlements, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vested computed in _getClaimable` equals `claimedAmount[account]` and that no account can withdraw more than it put in.
