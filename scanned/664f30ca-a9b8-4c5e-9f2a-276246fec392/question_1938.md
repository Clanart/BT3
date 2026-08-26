# Q1938: ArbitrumMWomAirdrop.claim - claimedAmount written after the external value movement

## Question
In rewards/ArbitrumMWomAirdrop.sol, claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Can an unprivileged attacker reach this through `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` while the claimant sets isLock to true so the vlMGP lock leg runs, and drive `vestingPeriodCount and intervals` out of agreement with `the elapsed period count` - breaking the invariant that the claimed counter must be written before the value it authorises leaves the contract - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ArbitrumMWomAirdrop.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: the claimant sets isLock to true so the vlMGP lock leg runs.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the claimant sets isLock to true so the vlMGP lock leg runs, asserting at the end that `vestingPeriodCount and intervals` still equals `the elapsed period count` and the PoC's balance delta is non-positive.
