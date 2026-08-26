# Q0974: AnkrBNBPoolHelper.deposit - stray receipt tokens on the helper are swept into the next deposit

## Question
Note that in wombat/AnkrBNBPoolHelper.sol, the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount, uint256 _minimumLiquidity)` under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested and force `this.balance(msg.sender)` apart from `lockedAmount[msg.sender]`, breaking the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit for Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount, uint256 _minimumLiquidity)`: constrain the setup so that the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, fuzz the attacker inputs (_amount and _minimumLiquidity), and assert after every call that a helper must never credit a depositor with receipt tokens it did not mint for that deposit.
