# Q2030: ArbitrumMWomAirdrop.claim - vested minus claimed can underflow and brick the claim

## Question
Note that in rewards/ArbitrumMWomAirdrop.sol, _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the claimant sets isLock to false so the plain transfer leg runs and force `vestingPeriodCount and intervals` apart from `the elapsed period count`, breaking the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: the claimant sets isLock to false so the plain transfer leg runs.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the claimant sets isLock to false so the plain transfer leg runs, snapshot `vestingPeriodCount and intervals` and `the elapsed period count`, run the attacker's `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
