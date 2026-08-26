# Q1343: ArbWomUp3.incentiveDeposit - safeApprove without reset across all three deposit modes

## Question
In wombat/ArbWomUp3.sol, _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Does `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` let an unprivileged caller exploit that under the MGP balance is below twice the capped reward, so that `_convertRatio supplied by the caller` diverges from `the buyback leg inside SmartWomConvert`, the invariant that every approval on a repeated deposit path must be idempotent is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset across all three deposit modes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Precondition: the MGP balance is below twice the capped reward.
- Invariant to test: every approval on a repeated deposit path must be idempotent; concretely, `_convertRatio supplied by the caller` must stay reconciled with `the buyback leg inside SmartWomConvert`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the MGP balance is below twice the capped reward, call `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`, and assert `_convertRatio supplied by the caller` equals `the buyback leg inside SmartWomConvert` and that no account can withdraw more than it put in.
