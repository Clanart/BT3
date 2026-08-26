# Q2742: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
wombat/WomUp.sol: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Under the attacker calls getReward immediately after a large stake by another user, is there an unprivileged sequence of `withdraw(uint256 amount, bool claim)` that leaves `rewards[account]` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that the contract must always hold at least _totalSupply of the redemption asset, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `rewards[account]` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls getReward immediately after a large stake by another user, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `rewards[account]` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
