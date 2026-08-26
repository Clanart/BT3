# Q3685: mWomSV.unlock - unlock settles through the permissionless multiclaimFor

## Question
In wombat/mWomSV.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Can an unprivileged attacker reach this through `unlock(uint256 _slotIndex)` while the attacker repeats cancelUnlock and startUnlock inside one transaction, and drive `totalAmount` out of agreement with `IERC20(mWOM).balanceOf(address(this))` - breaking the invariant that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: unlock settles through the permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker repeats cancelUnlock and startUnlock inside one transaction, have the attacker run `unlock(uint256 _slotIndex)`, then assert the victim's claimable value and the `totalAmount` versus `IERC20(mWOM).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
