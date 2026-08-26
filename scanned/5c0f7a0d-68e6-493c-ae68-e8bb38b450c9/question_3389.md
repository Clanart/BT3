# Q3389: mWomSV.unlock - unlock settles through the permissionless multiclaimFor

## Question
In wombat/mWomSV.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Can an unprivileged attacker reach this through `unlock(uint256 _slotIndex)` while the mWOM balance of the locker is exactly equal to totalAmount before the action, and drive `getUserAmountInCoolDown(user)` out of agreement with `totalAmountInCoolDown` - breaking the invariant that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: unlock settles through the permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `unlock(uint256 _slotIndex)`: constrain the setup so that the mWOM balance of the locker is exactly equal to totalAmount before the action, fuzz the attacker inputs (_slotIndex and the redemption timing), and assert after every call that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier.
