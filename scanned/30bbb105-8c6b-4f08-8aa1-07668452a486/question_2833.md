# Q2833: AnkrBNBPoolHelper.withdraw - withdraw releases the underlying before the stake check runs

## Question
In wombat/AnkrBNBPoolHelper.sol, withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Does `withdraw(uint256 _liquidity, uint256 _minAmount)` let an unprivileged caller exploit that under the caller sets _minAmount to zero on the withdrawal leg, so that `this.balance(msg.sender)` diverges from `lockedAmount[msg.sender]`, the invariant that an entitlement must be verified before the value backing it leaves the protocol is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the caller sets _minAmount to zero on the withdrawal leg.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the caller sets _minAmount to zero on the withdrawal leg, fuzz the attacker inputs (_liquidity, _minAmount and the ordering against the lockedAmount check), and assert after every call that an entitlement must be verified before the value backing it leaves the protocol.
