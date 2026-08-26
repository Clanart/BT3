# Q2722: WomUp.withdraw - stake takes WOM but withdraw pays mWOM

## Question
Consider wombat/WomUp.sol, where stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Assuming the attacker calls getReward immediately after a large stake by another user, can an unprivileged attacker turn this into a divergence between `rewardPerTokenStored` and `userRewardPerTokenPaid[account]` via `withdraw(uint256 amount, bool claim)`, breaking the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the attacker calls getReward immediately after a large stake by another user.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[account]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls getReward immediately after a large stake by another user, have the attacker run `withdraw(uint256 amount, bool claim)`, then assert the victim's claimable value and the `rewardPerTokenStored` versus `userRewardPerTokenPaid[account]` relation are unchanged by the attacker's transaction.
