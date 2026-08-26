# Q0428: ArbWomUp.incentiveDeposit - the deposited WOM has no redemption path in this contract

## Question
wombat/ArbWomUp.sol: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. With _amount with no per-user or global cap, and how many times the call is repeated under attacker control and the contract has just been topped up with USDT, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount)` so that `usdtReward` and `IERC20(usdt).balanceOf(address(this))` no longer reconcile, violating the invariant that a one-way deposit must be matched by an entitlement that cannot be under-recorded and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the deposited WOM has no redemption path in this contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Precondition: the contract has just been topped up with USDT.
- Invariant to test: a one-way deposit must be matched by an entitlement that cannot be under-recorded; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the contract has just been topped up with USDT, snapshot `usdtReward` and `IERC20(usdt).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
