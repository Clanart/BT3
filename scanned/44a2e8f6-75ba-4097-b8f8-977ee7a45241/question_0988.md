# Q0988: ArbWomUp3.incentiveDeposit - safeApprove without reset across all three deposit modes

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Assuming the caller sets _mode to a value other than 1 or 2, can an unprivileged attacker turn this into a divergence between `bracketRewarded` and `calDoubledCounted(account)` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that every approval on a repeated deposit path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset across all three deposit modes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Precondition: the caller sets _mode to a value other than 1 or 2.
- Invariant to test: every approval on a repeated deposit path must be idempotent; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`: constrain the setup so that the caller sets _mode to a value other than 1 or 2, fuzz the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer), and assert after every call that every approval on a repeated deposit path must be idempotent.
