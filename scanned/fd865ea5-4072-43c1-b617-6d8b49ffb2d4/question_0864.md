# Q0864: ArbWomUp3.incentiveDeposit - the caller sets the conversion ratio for the protocol's own routing

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Assuming the caller sets _mode to a value other than 1 or 2, can an unprivileged attacker turn this into a divergence between `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the caller sets the conversion ratio for the protocol's own routing)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Precondition: the caller sets _mode to a value other than 1 or 2.
- Invariant to test: a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path; concretely, `IERC20(mWom).balanceOf(address(this))` must stay reconciled with `the amount locked for _account in mode two`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _mode to a value other than 1 or 2, snapshot `IERC20(mWom).balanceOf(address(this))` and `the amount locked for _account in mode two`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
