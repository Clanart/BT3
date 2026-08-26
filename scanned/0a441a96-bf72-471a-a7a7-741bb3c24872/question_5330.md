# Q5330: WombatPoolHelperV2.withdraw - withdraw releases the underlying before the stake check runs

## Question
wombat/WombatPoolHelperV2.sol: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Under the attacker deposits and withdraws through the helper inside one transaction, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `IERC20(stakingToken).totalSupply()` unreconciled with `the MasterWombat staked balance for pid`, violates the invariant that an entitlement must be verified before the value backing it leaves the protocol, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the attacker deposits and withdraws through the helper inside one transaction.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_liquidity and _minAmount) under the attacker deposits and withdraws through the helper inside one transaction, asserting on every row that an entitlement must be verified before the value backing it leaves the protocol.
