# Q1474: ArbWomUp.incentiveDeposit - the deposited WOM has no redemption path in this contract

## Question
wombat/ArbWomUp.sol: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Under the caller has already claimed most of their tier entitlement, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount)` that leaves `usdtReward` unreconciled with `IERC20(usdt).balanceOf(address(this))`, violates the invariant that a one-way deposit must be matched by an entitlement that cannot be under-recorded, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the deposited WOM has no redemption path in this contract)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the incentive is paid immediately while the WOM taken by _deposit has no withdraw function on this contract, so the deposit leg is one-way and the entitlement rests entirely on the tier accounting being correct. Precondition: the caller has already claimed most of their tier entitlement.
- Invariant to test: a one-way deposit must be matched by an entitlement that cannot be under-recorded; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller has already claimed most of their tier entitlement, call `incentiveDeposit(uint256 _amount)`, and assert `usdtReward` equals `IERC20(usdt).balanceOf(address(this))` and that no account can withdraw more than it put in.
