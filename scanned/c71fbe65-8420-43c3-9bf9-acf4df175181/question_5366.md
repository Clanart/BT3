# Q5366: AnkrBNBPoolHelper.deposit - no reentrancy guard anywhere on the helper

## Question
In wombat/AnkrBNBPoolHelper.sol, none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Can an unprivileged attacker reach this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` while the receipt token is minted to the helper while the credit is directed at a different address, and drive `IERC20(stakingToken).totalSupply()` out of agreement with `the MasterWombat staked balance for pid` - breaking the invariant that the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: no reentrancy guard anywhere on the helper)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: none of deposit, depositLP, depositNative, withdraw or harvest carries nonReentrant, so the only protection is WombatStaking's own guard and any callback token on the deposit-token or receipt-token path re-enters the helper freely. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: the deposit and withdrawal helper must hold its own reentrancy domain rather than relying on a downstream guard; concretely, `IERC20(stakingToken).totalSupply()` must stay reconciled with `the MasterWombat staked balance for pid`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the receipt token is minted to the helper while the credit is directed at a different address, then assert `IERC20(stakingToken).totalSupply()` and `the MasterWombat staked balance for pid` end identical in both runs.
