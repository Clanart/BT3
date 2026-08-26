# Q1309: Airdrop2.claim - period count truncates so a whole interval can be lost

## Question
Note that in rewards/Airdrop2.sol, vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the account has already claimed the initial five percent tranche and force `vested computed in _getClaimable` apart from `claimedAmount[account]`, breaking the invariant that a vesting curve must not permanently penalise a claim placed near a period boundary for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: period count truncates so a whole interval can be lost)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: vested folds in (block.timestamp - startVestingTime) / intervals as an integer count before multiplying, so value accrues in discrete jumps and a claim placed just before a boundary permanently locks in the lower figure for the amount claimed. Precondition: the account has already claimed the initial five percent tranche.
- Invariant to test: a vesting curve must not permanently penalise a claim placed near a period boundary; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the account has already claimed the initial five percent tranche, have the attacker run `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, then assert the victim's claimable value and the `vested computed in _getClaimable` versus `claimedAmount[account]` relation are unchanged by the attacker's transaction.
