# Q0331: Airdrop2.claim - claimedAmount is keyed by account but the entitlement is keyed by leaf

## Question
Consider rewards/Airdrop2.sol, where claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Assuming block.timestamp is one second before an interval boundary, can an unprivileged attacker turn this into a divergence between `vested computed in _getClaimable` and `claimedAmount[account]` via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, breaking the invariant that the claimed counter must be scoped to the exact leaf that authorised the entitlement and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount is keyed by account but the entitlement is keyed by leaf)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Precondition: block.timestamp is one second before an interval boundary.
- Invariant to test: the claimed counter must be scoped to the exact leaf that authorised the entitlement; concretely, `vested computed in _getClaimable` must stay reconciled with `claimedAmount[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`: constrain the setup so that block.timestamp is one second before an interval boundary, fuzz the attacker inputs (totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing), and assert after every call that the claimed counter must be scoped to the exact leaf that authorised the entitlement.
