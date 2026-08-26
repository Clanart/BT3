# Q0951: Airdrop2.claim - claimedAmount is keyed by account but the entitlement is keyed by leaf

## Question
Note that in rewards/Airdrop2.sol, claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the elapsed period count has already exceeded vestingPeriodCount and force `vestingPeriodCount and intervals` apart from `the elapsed period count`, breaking the invariant that the claimed counter must be scoped to the exact leaf that authorised the entitlement for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: claimedAmount is keyed by account but the entitlement is keyed by leaf)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claimedAmount[account] is a single counter while _getClaimable is parameterised by the totalAmount carried in the proof, so an account that appears in the tree under more than one amount shares one counter across two different entitlements. Precondition: the elapsed period count has already exceeded vestingPeriodCount.
- Invariant to test: the claimed counter must be scoped to the exact leaf that authorised the entitlement; concretely, `vestingPeriodCount and intervals` must stay reconciled with `the elapsed period count`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the elapsed period count has already exceeded vestingPeriodCount, call `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`, and assert `vestingPeriodCount and intervals` equals `the elapsed period count` and that no account can withdraw more than it put in.
