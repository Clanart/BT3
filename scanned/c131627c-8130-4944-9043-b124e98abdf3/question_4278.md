# Q4278: WombatPoolHelper.harvest - V1 exposes no depositFor so every credit is msg.sender

## Question
Note that in wombat/WombatPoolHelper.sol, WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Can an attacker holding only tokens bought on market reach it via `harvest()` under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes and force `_minimumLiquidity supplied by the caller` apart from `the LP actually minted by the Wombat pool`, breaking the invariant that the single attribution path must still guarantee that minted receipts and credited stake are equal for Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `harvest()` (mechanism: V1 exposes no depositFor so every credit is msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `harvest()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which the pool's rewards are harvested and fee-split
- Exploit idea: WombatPoolHelper.sol has no depositFor, so _deposit is always called with _for equal to msg.sender, which makes the receipt-mint-to-helper and credit-to-caller mismatch the only attribution gap on this contract. Precondition: the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes.
- Invariant to test: the single attribution path must still guarantee that minted receipts and credited stake are equal; concretely, `_minimumLiquidity supplied by the caller` must stay reconciled with `the LP actually minted by the Wombat pool`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `harvest()` sequence atomically under the pool was deactivated in WombatStaking so _onlyActivePoolHelper rejects deposits while withdraw still passes, asserting at the end that `_minimumLiquidity supplied by the caller` still equals `the LP actually minted by the Wombat pool` and the PoC's balance delta is non-positive.
