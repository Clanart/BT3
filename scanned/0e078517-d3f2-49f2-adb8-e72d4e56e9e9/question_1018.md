# Q1018: ArbWomUp2.incentiveDeposit - the caller sets the slippage floor for a swap of the contract's tokens

## Question
wombat/ArbWomUp2.sol: the _bullMode branch calls _bullMGP(rewardToSend, _minMGPRec, msg.sender) with a caller-supplied minimum, and the swap spends the contract's own balance, so the caller decides how badly the protocol's tokens may be executed. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller splits the deposit across several addresses, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk` no longer reconcile, violating the invariant that the slippage floor on a swap of protocol-owned value must be derived from protocol state and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the caller sets the slippage floor for a swap of the contract's tokens)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the _bullMode branch calls _bullMGP(rewardToSend, _minMGPRec, msg.sender) with a caller-supplied minimum, and the swap spends the contract's own balance, so the caller decides how badly the protocol's tokens may be executed. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: the slippage floor on a swap of protocol-owned value must be derived from protocol state; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens) under the caller splits the deposit across several addresses, asserting on every row that the slippage floor on a swap of protocol-owned value must be derived from protocol state.
