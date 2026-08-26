# Q0982: Airdrop2.claim - vested minus claimed can underflow and brick the claim

## Question
Note that in rewards/Airdrop2.sol, _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the elapsed period count has already exceeded vestingPeriodCount and force `claimable` apart from `reward.balanceOf(address(this))`, breaking the invariant that a vesting accessor must never be able to permanently block an account's remaining entitlement for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: vested minus claimed can underflow and brick the claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: _getClaimable() returns vested - claimed after only guarding claimed >= totalAmount, so any state where claimed sits above the currently vested figure makes every further claim revert for that account. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: a vesting accessor must never be able to permanently block an account's remaining entitlement; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the elapsed period count has already exceeded vestingPeriodCount, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `claimable` versus `reward.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
