# Q1705: ArbWomUp2.incentiveDeposit - the bull swap is sandwichable by the same caller

## Question
wombat/ArbWomUp2.sol: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and userWOMDeposited is still zero for the caller, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `bullBonusRatio` and `DENOMINATOR` no longer reconcile, violating the invariant that a protocol-owned swap must not be executable at a price the initiator sets in the same transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull swap is sandwichable by the same caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: a protocol-owned swap must not be executable at a price the initiator sets in the same transaction; concretely, `bullBonusRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence atomically under userWOMDeposited is still zero for the caller, asserting at the end that `bullBonusRatio` still equals `DENOMINATOR` and the PoC's balance delta is non-positive.
