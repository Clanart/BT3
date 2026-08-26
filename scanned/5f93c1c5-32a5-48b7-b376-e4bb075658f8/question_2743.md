# Q2743: ArbWomUp3.incentiveDeposit - safeApprove without reset across all three deposit modes

## Question
In wombat/ArbWomUp3.sol, _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Does `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` let an unprivileged caller exploit that under the caller crosses several tier boundaries in one deposit, so that `bracketRewarded` diverges from `calDoubledCounted(account)`, the invariant that every approval on a repeated deposit path must be idempotent is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset across all three deposit modes)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() approves mWom, smartWomConvert and mWomSV in sequence without zeroing any of them, so a single under-consuming target bricks the whole deposit path. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: every approval on a repeated deposit path must be idempotent; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer) under the caller crosses several tier boundaries in one deposit, asserting on every row that every approval on a repeated deposit path must be idempotent.
