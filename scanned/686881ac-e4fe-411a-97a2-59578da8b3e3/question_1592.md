# Q1592: WombatPoolHelper.depositNative - V1 exposes no depositFor so every credit is msg.sender

## Question
Note that in wombat/WombatPoolHelper.sol, WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Can an attacker holding only tokens bought on market reach it via `depositNative(uint256 _minimumLiquidity)` under the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested and force `_minimumLiquidity supplied by the caller` apart from `the LP actually minted by the Wombat pool`, breaking the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `depositNative(uint256 _minimumLiquidity)` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositNative(uint256 _minimumLiquidity)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: msg.value and _minimumLiquidity
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `depositNative(uint256 _minimumLiquidity)`: constrain the setup so that the pool's deposit token charges a transfer fee so the Wombat deposit receives less than requested, fuzz the attacker inputs (msg.value and _minimumLiquidity), and assert after every call that the single attribution path must still guarantee that minted receipts and credited stake are equal.
