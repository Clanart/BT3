# Q5477: WombatPoolHelperV2.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
wombat/WombatPoolHelperV2.sol: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Under the receipt token is minted to the helper while the credit is directed at a different address, is there an unprivileged sequence of `depositLP(uint256 _lpAmount)` that leaves `_liquidity burned via burnReceiptToken` unreconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`, violates the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the receipt token is minted to the helper while the credit is directed at a different address.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `_liquidity burned via burnReceiptToken` must stay reconciled with `the deposit-token balance delta paid out by WombatStaking.withdraw`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lpAmount) under the receipt token is minted to the helper while the credit is directed at a different address, asserting on every row that a helper must never credit a depositor with receipt tokens it did not mint for that deposit.
