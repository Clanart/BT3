# Q1449: ArbWomUp2.incentiveDeposit - calDoubledCounted is read from mutable balance state

## Question
wombat/ArbWomUp2.sol: the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. Under the caller crosses several tier boundaries in one deposit, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` that leaves `_minMGPRec supplied by the caller` unreconciled with `the MGP actually received by the swap`, violates the invariant that a double-count correction must be a monotonic stored record, not a live balance read, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: calDoubledCounted is read from mutable balance state)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the double-count correction is derived from a live balance read rather than from a stored per-user record of what was already rewarded, so any action that lowers that balance lowers the correction and re-opens the same tier. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a double-count correction must be a monotonic stored record, not a live balance read; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence atomically under the caller crosses several tier boundaries in one deposit, asserting at the end that `_minMGPRec supplied by the caller` still equals `the MGP actually received by the swap` and the PoC's balance delta is non-positive.
