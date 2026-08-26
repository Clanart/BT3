# Q1447: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
In wombat/WomUp.sol, withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Can an unprivileged attacker reach this through `withdraw(uint256 amount, bool claim)` while the reward period has just ended so periodFinish is behind block.timestamp, and drive `rewardRate * duration` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that the contract must always hold at least _totalSupply of the redemption asset - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `rewardRate * duration` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the reward period has just ended so periodFinish is behind block.timestamp, call `withdraw(uint256 amount, bool claim)`, and assert `rewardRate * duration` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
