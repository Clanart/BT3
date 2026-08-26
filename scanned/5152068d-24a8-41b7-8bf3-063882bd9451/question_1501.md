# Q1501: ArbWomUp2.incentiveDeposit - the bull swap is sandwichable by the same caller

## Question
In wombat/ArbWomUp2.sol, because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. Does `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` let an unprivileged caller exploit that under the router pair for the bull swap holds thin liquidity, so that `_minMGPRec supplied by the caller` diverges from `the MGP actually received by the swap`, the invariant that a protocol-owned swap must not be executable at a price the initiator sets in the same transaction is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull swap is sandwichable by the same caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. Precondition: the router pair for the bull swap holds thin liquidity.
- Invariant to test: a protocol-owned swap must not be executable at a price the initiator sets in the same transaction; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`: constrain the setup so that the router pair for the bull swap holds thin liquidity, fuzz the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens), and assert after every call that a protocol-owned swap must not be executable at a price the initiator sets in the same transaction.
