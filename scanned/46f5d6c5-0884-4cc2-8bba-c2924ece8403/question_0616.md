# Q0616: ArbWomUp3.incentiveDeposit - safeApprove without reset across all three deposit modes

## Question
Note that in wombat/ArbWomUp3.sol, _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller sets _mode to 2 so the doubling applies and force `rewardToSend` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that every approval on a repeated deposit path must be idempotent for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset across all three deposit modes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Precondition: the caller sets _mode to 2 so the doubling applies.
- Invariant to test: every approval on a repeated deposit path must be idempotent; concretely, `rewardToSend` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the caller sets _mode to 2 so the doubling applies, snapshot `rewardToSend` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
