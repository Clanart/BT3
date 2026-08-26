# Q2748: mWomSV.unlock - unlock settles through the permissionless multiclaimFor

## Question
wombat/mWomSV.sol: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, is there an unprivileged sequence of `unlock(uint256 _slotIndex)` that leaves `mWomSV.getUserTotalLocked(user)` unreconciled with `ArbWomUp3.calDoubledCounted(user)`, violates the invariant that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: unlock settles through the permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, then assert `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` end identical in both runs.
