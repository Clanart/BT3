# Q1244: WombatPoolHelper.deposit - V1 exposes no depositFor so every credit is msg.sender

## Question
In wombat/WombatPoolHelper.sol, WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Can an unprivileged attacker reach this through `deposit(uint256 _amount, uint256 _minimumLiquidity)` while the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, and drive `this.balance(msg.sender)` out of agreement with `lockedAmount[msg.sender]` - breaking the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal - for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `deposit(uint256 _amount, uint256 _minimumLiquidity)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount, uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _minimumLiquidity, forwarded verbatim into the Wombat pool
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `this.balance(msg.sender)` must stay reconciled with `lockedAmount[msg.sender]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount, uint256 _minimumLiquidity)` sequence atomically under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, asserting at the end that `this.balance(msg.sender)` still equals `lockedAmount[msg.sender]` and the PoC's balance delta is non-positive.
