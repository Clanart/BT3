# Q1423: ArbWomUp2.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
wombat/ArbWomUp2.sol - the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Can an unprivileged attacker controlling _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens, under the caller crosses several tier boundaries in one deposit, exploit this through `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` to break the reconciliation between `rewardToSend` and `IERC20(busd).balanceOf(address(this))` and the invariant that an incentive pot must not be fully claimable by a single actor in one transaction, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller crosses several tier boundaries in one deposit, have the attacker run `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, then assert the victim's claimable value and the `rewardToSend` versus `IERC20(busd).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
