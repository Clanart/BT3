# Q5338: AnkrBNBPoolHelper.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
Consider wombat/AnkrBNBPoolHelper.sol, where the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Assuming the receipt token is minted to the helper while the credit is directed at a different address, can an unprivileged attacker turn this into a divergence between `pid cached at construction` and `pools[lpToken].pid in WombatStaking` via `deposit(uint256 _amount, uint256 _minimumLiquidity)`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `pid cached at construction` must stay reconciled with `pools[lpToken].pid in WombatStaking`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the receipt token is minted to the helper while the credit is directed at a different address, snapshot `pid cached at construction` and `pools[lpToken].pid in WombatStaking`, run the attacker's `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
