# Q0460: ArbWomUp2.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
wombat/ArbWomUp2.sol: the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller sets _bullMode to false so the plain transfer branch runs, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `_minMGPRec supplied by the caller` and `the MGP actually received by the swap` no longer reconcile, violating the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Precondition: the caller sets _bullMode to false so the plain transfer branch runs.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence atomically under the caller sets _bullMode to false so the plain transfer branch runs, asserting at the end that `_minMGPRec supplied by the caller` still equals `the MGP actually received by the swap` and the PoC's balance delta is non-positive.
