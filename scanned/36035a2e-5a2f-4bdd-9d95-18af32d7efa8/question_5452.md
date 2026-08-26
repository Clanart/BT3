# Q5452: WombatPoolHelper.withdraw - withdraw releases the underlying before the stake check runs

## Question
In wombat/WombatPoolHelper.sol, withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Can an unprivileged attacker reach this through `withdraw(uint256 _liquidity, uint256 _minAmount)` while the attacker deposits and withdraws through the helper inside one transaction, and drive `this.balance(msg.sender)` out of agreement with `lockedAmount[msg.sender]` - breaking the invariant that an entitlement must be verified before the value backing it leaves the protocol - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the attacker deposits and withdraws through the helper inside one transaction, fuzz the attacker inputs (_liquidity and _minAmount, with the payout measured as a balance delta), and assert after every call that an entitlement must be verified before the value backing it leaves the protocol.
