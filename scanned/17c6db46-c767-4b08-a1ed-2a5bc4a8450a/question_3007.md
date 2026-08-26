# Q3007: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
In wombat/WomUp.sol, withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Can an unprivileged attacker reach this through `withdraw(uint256 amount, bool claim)` while the attacker stakes one wei so _totalSupply is non-zero but every division truncates, and drive `lastUpdateTime` out of agreement with `periodFinish` - breaking the invariant that the contract must always hold at least _totalSupply of the redemption asset - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the attacker stakes one wei so _totalSupply is non-zero but every division truncates.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker stakes one wei so _totalSupply is non-zero but every division truncates, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `lastUpdateTime` versus `periodFinish` relation are unchanged by the attacker's transaction.
