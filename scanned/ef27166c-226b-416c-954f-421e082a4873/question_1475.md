# Q1475: ArbWomUp2.incentiveDeposit - the caller sets the slippage floor for a swap of the contract's tokens

## Question
Consider wombat/ArbWomUp2.sol, where the _bullMode branch calls _bullMGP(rewardToSend, _minMGPRec, msg.sender) with a caller-supplied minimum, and the swap spends the contract's own balance, so the caller decides how badly the protocol's tokens may be executed. Assuming the router pair for the bull swap holds thin liquidity, can an unprivileged attacker turn this into a divergence between `rewardToSend` and `IERC20(busd).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, breaking the invariant that the slippage floor on a swap of protocol-owned value must be derived from protocol state and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the caller sets the slippage floor for a swap of the contract's tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the _bullMode branch calls _bullMGP(rewardToSend, _minMGPRec, msg.sender) with a caller-supplied minimum, and the swap spends the contract's own balance, so the caller decides how badly the protocol's tokens may be executed. Precondition: the router pair for the bull swap holds thin liquidity.
- Invariant to test: the slippage floor on a swap of protocol-owned value must be derived from protocol state; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the router pair for the bull swap holds thin liquidity, then assert `rewardToSend` and `IERC20(busd).balanceOf(address(this))` end identical in both runs.
