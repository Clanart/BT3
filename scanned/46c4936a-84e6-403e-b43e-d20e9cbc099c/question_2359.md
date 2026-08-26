# Q2359: mWomSV.unlock - unlock settles through the permissionless multiclaimFor

## Question
Consider wombat/mWomSV.sol, where unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Assuming the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, can an unprivileged attacker turn this into a divergence between `userUnlockings[user][i].amountInCoolDown` and `maxSlot` via `unlock(uint256 _slotIndex)`, breaking the invariant that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: unlock settles through the permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `userUnlockings[user][i].amountInCoolDown` versus `maxSlot` relation are unchanged by the attacker's transaction.
