# Q2953: ArbWomUp3.incentiveDeposit - safeApprove without reset on the vlMGP reward leg

## Question
Note that in wombat/ArbWomUp3.sol, incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under the caller repeats the deposit from several addresses in one block and force `bracketRewarded` apart from `calDoubledCounted(account)`, breaking the invariant that an approval on a repeated path must be idempotent for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: safeApprove without reset on the vlMGP reward leg)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: incentiveDeposit() calls IERC20(mgp).safeApprove(address(vlMGP), rewardToSend) with no prior zeroing, so residue from a lockFor that under-consumes permanently disables the incentive for every participant. Precondition: the caller repeats the deposit from several addresses in one block.
- Invariant to test: an approval on a repeated path must be idempotent; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer) under the caller repeats the deposit from several addresses in one block, asserting on every row that an approval on a repeated path must be idempotent.
