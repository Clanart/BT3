# Q1508: mWomSV.unlock - unlock settles through the permissionless multiclaimFor

## Question
wombat/mWomSV.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Under the attacker reached maxSlot so slot reuse is forced, is there an unprivileged sequence of `unlock(uint256 _slotIndex)` that leaves `totalAmount` unreconciled with `IERC20(mWOM).balanceOf(address(this))`, violates the invariant that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: unlock settles through the permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the redemption timing) under the attacker reached maxSlot so slot reuse is forced, asserting on every row that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier.
