# Q1049: ArbWomUp2.incentiveDeposit - the bull swap is sandwichable by the same caller

## Question
wombat/ArbWomUp2.sol: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller splits the deposit across several addresses, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `claimedReward[account]` and `userWOMDeposited[account]` no longer reconcile, violating the invariant that a protocol-owned swap must not be executable at a price the initiator sets in the same transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull swap is sandwichable by the same caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: a protocol-owned swap must not be executable at a price the initiator sets in the same transaction; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller splits the deposit across several addresses, then assert `claimedReward[account]` and `userWOMDeposited[account]` end identical in both runs.
