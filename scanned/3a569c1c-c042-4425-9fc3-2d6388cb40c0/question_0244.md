# Q0244: ArbWomUp3.incentiveDeposit - safeApprove without reset across all three deposit modes

## Question
Consider wombat/ArbWomUp3.sol, where _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Assuming the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, can an unprivileged attacker turn this into a divergence between `rewardToSend after the _mode == 2 doubling` and `the mgpleft cap applied inside getRewardAmount` via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, breaking the invariant that every approval on a repeated deposit path must be idempotent and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset across all three deposit modes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Precondition: the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block.
- Invariant to test: every approval on a repeated deposit path must be idempotent; concretely, `rewardToSend after the _mode == 2 doubling` must stay reconciled with `the mgpleft cap applied inside getRewardAmount`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the caller deposits, calls mWomSV.startUnlock and deposits again inside the same block, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, then assert the victim's claimable value and the `rewardToSend after the _mode == 2 doubling` versus `the mgpleft cap applied inside getRewardAmount` relation are unchanged by the attacker's transaction.
