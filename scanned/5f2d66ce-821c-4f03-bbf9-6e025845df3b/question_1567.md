# Q1567: WombatPoolHelperV2.withdraw - withdraw releases the underlying before the stake check runs

## Question
wombat/WombatPoolHelperV2.sol - withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Can an unprivileged attacker controlling _liquidity and _minAmount, under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, exploit this through `withdraw(uint256 _liquidity, uint256 _minAmount)` to break the reconciliation between `_liquidity burned via burnReceiptToken` and `the deposit-token balance delta paid out by WombatStaking.withdraw` and the invariant that an entitlement must be verified before the value backing it leaves the protocol, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: withdraw releases the underlying before the stake check runs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount
- Exploit idea: withdraw() calls IWombatStaking(wombatStaking).withdraw first, which sends the deposit token to msg.sender, and only afterwards calls _unstake, which is the step that actually verifies the caller had that much staked in MasterMagpie. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: an entitlement must be verified before the value backing it leaves the protocol; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, call `withdraw(uint256 _liquidity, uint256 _minAmount)`, and assert `_liquidity burned via burnReceiptToken` equals `the deposit-token balance delta paid out by WombatStaking.withdraw` and that no account can withdraw more than it put in.
