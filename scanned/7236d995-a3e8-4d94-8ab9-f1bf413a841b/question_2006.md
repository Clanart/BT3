# Q2006: Airdrop2.claim - claimedAmount is keyed by account but the entitlement is keyed by leaf

## Question
In rewards/Airdrop2.sol, claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Does `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` let an unprivileged caller exploit that under the claimant sets isLock to false so the plain transfer leg runs, so that `startVestingTime` diverges from `block.timestamp`, the invariant that the claimed counter must be scoped to the exact leaf that authorised the entitlement is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount is keyed by account but the entitlement is keyed by leaf)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Precondition: the claimant sets isLock to false so the plain transfer leg runs.
- Invariant to test: the claimed counter must be scoped to the exact leaf that authorised the entitlement; concretely, `startVestingTime` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the claimant sets isLock to false so the plain transfer leg runs, then assert `startVestingTime` and `block.timestamp` end identical in both runs.
