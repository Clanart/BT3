# Q2123: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
wombat/WomUp.sol - withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Can an unprivileged attacker controlling amount and whether the claim leg runs in the same call, under the attacker migrates and withdraws inside one transaction, exploit this through `withdraw(uint256 amount, bool claim)` to break the reconciliation between `_totalSupply` and `IERC20(mWom).balanceOf(address(this))` and the invariant that the contract must always hold at least _totalSupply of the redemption asset, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the attacker migrates and withdraws inside one transaction.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `_totalSupply` must stay reconciled with `IERC20(mWom).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker migrates and withdraws inside one transaction, snapshot `_totalSupply` and `IERC20(mWom).balanceOf(address(this))`, run the attacker's `withdraw(uint256 amount, bool claim)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
