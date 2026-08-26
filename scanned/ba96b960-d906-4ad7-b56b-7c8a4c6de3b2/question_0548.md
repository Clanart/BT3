# Q0548: Airdrop2.claim - claimedAmount written after the external value movement

## Question
Consider rewards/Airdrop2.sol, where claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `vestingPeriodCount and intervals` and `the elapsed period count` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that the claimed counter must be written before the value it authorises leaves the contract and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount written after the external value movement)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() performs the lock or transfer first and only then writes claimedAmount[msg.sender] = userClaimedAmount + claimable, relying entirely on the nonReentrant modifier rather than on ordering. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: the claimed counter must be written before the value it authorises leaves the contract; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that block.timestamp is one second before an interval boundary, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that the claimed counter must be written before the value it authorises leaves the contract.
