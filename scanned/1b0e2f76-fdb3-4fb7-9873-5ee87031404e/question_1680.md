# Q1680: ArbWomUp2.incentiveDeposit - the caller sets the slippage floor for a swap of the contract's tokens

## Question
wombat/ArbWomUp2.sol: the _bullMode branch calls _bullMGP(rewardToSend, _minMGPRec, msg.sender) with a caller-supplied minimum, and the swap spends the contract's own balance, so the caller decides how badly the protocol's tokens may be executed. Under userWOMDeposited is still zero for the caller, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` that leaves `_minMGPRec supplied by the caller` unreconciled with `the MGP actually received by the swap`, violates the invariant that the slippage floor on a swap of protocol-owned value must be derived from protocol state, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the caller sets the slippage floor for a swap of the contract's tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the _bullMode branch calls _bullMGP(rewardToSend, _minMGPRec, msg.sender) with a caller-supplied minimum, and the swap spends the contract's own balance, so the caller decides how badly the protocol's tokens may be executed. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: the slippage floor on a swap of protocol-owned value must be derived from protocol state; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens) under userWOMDeposited is still zero for the caller, asserting on every row that the slippage floor on a swap of protocol-owned value must be derived from protocol state.
