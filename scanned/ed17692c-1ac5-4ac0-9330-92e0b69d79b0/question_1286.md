# Q1286: ArbWomUp2.incentiveDeposit - the bull swap is sandwichable by the same caller

## Question
Note that in wombat/ArbWomUp2.sol, because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` under the caller crosses several tier boundaries in one deposit and force `rewardToSend` apart from `IERC20(busd).balanceOf(address(this))`, breaking the invariant that a protocol-owned swap must not be executable at a price the initiator sets in the same transaction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the bull swap is sandwichable by the same caller)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: because _minMGPRec can be set to zero and the swap runs against a public router in the caller's own transaction, the caller can move the pool immediately before and after and capture the difference. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a protocol-owned swap must not be executable at a price the initiator sets in the same transaction; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller crosses several tier boundaries in one deposit, then assert `rewardToSend` and `IERC20(busd).balanceOf(address(this))` end identical in both runs.
