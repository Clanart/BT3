# Q4684: WombatPoolHelperV2.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
Note that in wombat/WombatPoolHelperV2.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Can an attacker holding only tokens bought on market reach it via `depositLP(uint256 _lpAmount)` under an active mWOM-flagged fee entry routes the harvest through SmartWomConvert and force `_minimumLiquidity supplied by the caller` apart from `the LP actually minted by the Wombat pool`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit for Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatPoolHelperV2.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: an active mWOM-flagged fee entry routes the harvest through SmartWomConvert.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange an active mWOM-flagged fee entry routes the harvest through SmartWomConvert, call `depositLP(uint256 _lpAmount)`, and assert `_minimumLiquidity supplied by the caller` equals `the LP actually minted by the Wombat pool` and that no account can withdraw more than it put in.
