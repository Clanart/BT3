# Q2445: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
Note that in wombat/WomUp.sol, withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 amount, bool claim)` under the MGP balance is below the sum of accrued rewards and force `rewardPerTokenStored` apart from `userRewardPerTokenPaid[account]`, breaking the invariant that the contract must always hold at least _totalSupply of the redemption asset for Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the MGP balance is below the sum of accrued rewards.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the MGP balance is below the sum of accrued rewards, call `withdraw(uint256 amount, bool claim)`, and assert `rewardPerTokenStored` equals `userRewardPerTokenPaid[account]` and that no account can withdraw more than it put in.
