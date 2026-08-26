# Q3363: AnkrBNBPoolHelper.withdraw - withdraw releases the underlying before the stake check runs

## Question
wombat/AnkrBNBPoolHelper.sol: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Under a residual stakingToken balance from an earlier rounding sits on the helper, is there an unprivileged sequence of `withdraw(uint256 _liquidity, uint256 _minAmount)` that leaves `IERC20(stakingToken).totalSupply()` unreconciled with `the MasterWombat staked balance for pid`, violates the invariant that an entitlement must be verified before the value backing it leaves the protocol, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity, _minAmount and the ordering against the lockedAmount check
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a residual stakingToken balance from an earlier rounding sits on the helper, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `IERC20(stakingToken).totalSupply()` equals `the MasterWombat staked balance for pid` and that no account can withdraw more than it put in.
