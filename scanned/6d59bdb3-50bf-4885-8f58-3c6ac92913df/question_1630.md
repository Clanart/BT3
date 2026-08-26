# Q1630: ArbWomUp2.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
In wombat/ArbWomUp2.sol, the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Starting from a state where the router pair for the bull swap holds thin liquidity, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to leave `_minMGPRec supplied by the caller` inconsistent with `the MGP actually received by the swap`, violating the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Precondition: the router pair for the bull swap holds thin liquidity.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the router pair for the bull swap holds thin liquidity, then assert `_minMGPRec supplied by the caller` and `the MGP actually received by the swap` end identical in both runs.
