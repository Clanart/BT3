# Q0120: ArbWomUp3.incentiveDeposit - the caller sets the conversion ratio for the protocol's own routing

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Assuming the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, can an unprivileged attacker turn this into a divergence between `bracketRewarded` and `calDoubledCounted(account)` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the caller sets the conversion ratio for the protocol's own routing)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Precondition: the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block.
- Invariant to test: a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, then assert `bracketRewarded` and `calDoubledCounted(account)` end identical in both runs.
