# Q0057: ArbWomUp2.incentiveDeposit - the bull swap is sandwichable by the same caller

## Question
wombat/ArbWomUp2.sol: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller sets _minMGPRec to zero and sandwiches the router pair, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `rewardToSend` and `IERC20(busd).balanceOf(address(this))` no longer reconcile, violating the invariant that a protocol-owned swap must not be executable at a price the initiator sets in the same transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull swap is sandwichable by the same caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. Precondition: the caller sets _minMGPRec to zero and sandwiches the router pair.
- Invariant to test: a protocol-owned swap must not be executable at a price the initiator sets in the same transaction; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets _minMGPRec to zero and sandwiches the router pair, call `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, and assert `rewardToSend` equals `IERC20(busd).balanceOf(address(this))` and that no account can withdraw more than it put in.
