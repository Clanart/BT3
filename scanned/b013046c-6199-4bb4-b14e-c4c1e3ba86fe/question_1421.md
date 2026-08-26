# Q1421: WomUp.withdraw - stake takes WOM but withdraw pays mWOM

## Question
In wombat/WomUp.sol, stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Does `withdraw(uint256 amount, bool claim)` let an unprivileged caller exploit that under the reward period has just ended so periodFinish is behind block.timestamp, so that `lastUpdateTime` diverges from `periodFinish`, the invariant that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/WomUp.sol -> `withdraw(uint256 amount, bool claim)` (mechanism: stake takes WOM but withdraw pays mWOM)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 amount, bool claim)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: amount and whether the claim leg runs in the same call
- Exploit idea: stake() pulls WOM and immediately calls mWom.deposit, while withdraw() transfers mWOM out of the contract's balance, so the ledger _balances is denominated in the deposited WOM while the payout is drawn from an mWOM pot shared with every other participant. Precondition: the reward period has just ended so periodFinish is behind block.timestamp.
- Invariant to test: the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger; concretely, `lastUpdateTime` must stay reconciled with `periodFinish`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 amount, bool claim)`: constrain the setup so that the reward period has just ended so periodFinish is behind block.timestamp, fuzz the attacker inputs (amount and whether the claim leg runs in the same call), and assert after every call that the unit a balance is credited in must be the unit it is redeemed in, out of a pot reserved for that ledger.
