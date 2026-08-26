# Q2466: Airdrop2.claim - claimedAmount is keyed by account but the entitlement is keyed by leaf

## Question
rewards/Airdrop2.sol: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. With totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing under attacker control and the computed claimable is exactly zero, can an unprivileged caller sequence `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` so that `claimable` and `reward.balanceOf(address(this))` no longer reconcile, violating the invariant that the claimed counter must be scoped to the exact leaf that authorised the entitlement and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount is keyed by account but the entitlement is keyed by leaf)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Precondition: the computed claimable is exactly zero.
- Invariant to test: the claimed counter must be scoped to the exact leaf that authorised the entitlement; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the computed claimable is exactly zero, then assert `claimable` and `reward.balanceOf(address(this))` end identical in both runs.
