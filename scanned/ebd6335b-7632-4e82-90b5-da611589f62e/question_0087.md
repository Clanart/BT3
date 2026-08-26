# Q0087: ArbWomUp.incentiveDeposit - the tier bonus is drained by a single oversized deposit

## Question
In wombat/ArbWomUp.sol, the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount)` while the contract's USDT balance is below the tier reward the deposit earned, and drive `usdtReward` out of agreement with `IERC20(usdt).balanceOf(address(this))` - breaking the invariant that an incentive pot must not be fully claimable by a single actor in one transaction - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier bonus is drained by a single oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Precondition: the contract's USDT balance is below the tier reward the deposit earned.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount)` sequence atomically under the contract's USDT balance is below the tier reward the deposit earned, asserting at the end that `usdtReward` still equals `IERC20(usdt).balanceOf(address(this))` and the PoC's balance delta is non-positive.
