# Q1282: ArbitrumMWomAirdrop.claim - vested minus claimed can underflow and brick the claim

## Question
Consider rewards/ArbitrumMWomAirdrop.sol, where _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Assuming the account has already claimed the initial five percent tranche, can an unprivileged attacker turn this into a divergence between `claimedAmount[account]` and `totalAmount proven by the merkle leaf` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `claimedAmount[account]` must stay reconciled with `totalAmount proven by the merkle leaf`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the account has already claimed the initial five percent tranche, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `claimedAmount[account]` versus `totalAmount proven by the merkle leaf` relation are unchanged by the attacker's transaction.
