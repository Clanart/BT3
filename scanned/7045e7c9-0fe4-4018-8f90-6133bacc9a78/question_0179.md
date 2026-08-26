# Q0179: WomUp.withdraw - withdraw draws from a shared mWOM balance with no reservation

## Question
wombat/WomUp.sol - withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Can an unprivileged attacker controlling amount and whether the claim leg runs in the same call, under the attacker is the only staker for a single block, exploit this through `withdraw(uint256 amount, bool claim)` to break the reconciliation between `rewardPerTokenStored` and `userRewardPerTokenPaid[account]` and the invariant that the contract must always hold at least _totalSupply of the redemption asset, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: withdraw draws from a shared mWOM balance with no reservation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: withdraw() reduces _balances and _totalSupply and then transfers mWOM out of whatever the contract holds, with no check that the remaining balance still covers the remaining _totalSupply. Precondition: the attacker is the only staker for a single block.
- Invariant to test: the contract must always hold at least _totalSupply of the redemption asset; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `withdraw(uint256 amount, bool claim)` sequence atomically under the attacker is the only staker for a single block, asserting at the end that `rewardPerTokenStored` still equals `userRewardPerTokenPaid[account]` and the PoC's balance delta is non-positive.
