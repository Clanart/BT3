# Q1945: mWomSV.unlock - unlock settles through the permissionless multiclaimFor

## Question
wombat/mWomSV.sol - unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Can an unprivileged attacker controlling _slotIndex and the redemption timing, under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, exploit this through `unlock(uint256 _slotIndex)` to break the reconciliation between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` and the invariant that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: unlock settles through the permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, then assert `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` end identical in both runs.
