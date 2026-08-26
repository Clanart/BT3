# Q1937: Airdrop2.claim - claimedAmount written after the external value movement

## Question
rewards/Airdrop2.sol: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Under the claimant sets isLock to true so the vlMGP lock leg runs, is there an unprivileged sequence of `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` that leaves `vestingPeriodCount and intervals` unreconciled with `the elapsed period count`, violates the invariant that the claimed counter must be written before the value it authorises leaves the contract, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the claimant sets isLock to true so the vlMGP lock leg runs, then assert `vestingPeriodCount and intervals` and `the elapsed period count` end identical in both runs.
