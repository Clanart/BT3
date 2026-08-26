# Q2565: VLMGP.unlock - totalAmount and the MasterMagpie stake are written in different transactions

## Question
In VLMGP.sol, _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Starting from a state where the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged EOA use `unlock(uint256 _slotIndex)` to leave `getUserAmountInCoolDown(user)` inconsistent with `totalAmountInCoolDown`, violating the invariant that the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point and extracting Critical - Protocol insolvency?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: totalAmount and the MasterMagpie stake are written in different transactions)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: _lock() calls depositVlMGPFor before incrementing totalAmount, and _unlock() calls withdrawVlMGPFor before decrementing it, so during the external call totalSupply() (which MasterMagpie uses as _calLpSupply for the vlMGP pool) disagrees with the credited stake. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: the vlMGP pool's supply denominator and the sum of credited stakes must agree at every observable point; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `unlock(uint256 _slotIndex)` sequence atomically under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, asserting at the end that `getUserAmountInCoolDown(user)` still equals `totalAmountInCoolDown` and the PoC's balance delta is non-positive.
