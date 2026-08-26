# Q0613: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
Note that in wombat/WomUp.sol, withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Can an attacker holding only tokens bought on market reach it via `withdraw(uint256 amount, bool claim)` under the attacker funds the stake with a flash loan of WOM repaid in the same transaction and force `rewards[account]` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that the contract must always hold at least _totalSupply of the redemption asset for Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the attacker funds the stake with a flash loan of WOM repaid in the same transaction.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker funds the stake with a flash loan of WOM repaid in the same transaction, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `rewards[account]` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
