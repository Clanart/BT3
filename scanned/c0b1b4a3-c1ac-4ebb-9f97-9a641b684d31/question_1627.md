# Q1627: Airdrop2.claim - no check that claimable is non-zero

## Question
Note that in rewards/Airdrop2.sol, claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Can an attacker holding only tokens bought on market reach it via `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` under the contract's reward balance is below the sum of unclaimed entitlements and force `claimable` apart from `reward.balanceOf(address(this))`, breaking the invariant that a claim that moves no value must revert rather than mutate state and emit for High - Theft of unclaimed yield?

## Target
- File/function: rewards/Airdrop2.sol -> `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` (mechanism: no check that claimable is non-zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: totalAmount and merkleProof for any leaf that verifies against the root, plus isLock and the claim timing
- Exploit idea: claim() proceeds even when _getClaimable returns zero, running the approve and lock legs with a zero amount and emitting a claim event, so the contract cannot distinguish a real claim from a no-op. Precondition: the contract's reward balance is below the sum of unclaimed entitlements.
- Invariant to test: a claim that moves no value must revert rather than mutate state and emit; concretely, `claimable` must stay reconciled with `reward.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claim(uint256 totalAmount, bytes32[] merkleProof, bool isLock)` sequence atomically under the contract's reward balance is below the sum of unclaimed entitlements, asserting at the end that `claimable` still equals `reward.balanceOf(address(this))` and the PoC's balance delta is non-positive.
