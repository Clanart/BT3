# Q1554: ArbWomUp3.incentiveDeposit - the caller sets the conversion ratio for the protocol's own routing

## Question
wombat/ArbWomUp3.sol - _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Can an unprivileged attacker controlling _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer, under the caller sets _convertRatio to zero so the whole leg is swapped, exploit this through `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` to break the reconciliation between `rewardToSend after the _mode == 2 doubling` and `the mgpleft cap applied inside getRewardAmount` and the invariant that a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the caller sets the conversion ratio for the protocol's own routing)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Precondition: the caller sets _convertRatio to zero so the whole leg is swapped.
- Invariant to test: a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer) under the caller sets _convertRatio to zero so the whole leg is swapped, asserting on every row that a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path.
