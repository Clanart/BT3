# Q3083: mWomSV.unlock - unlock settles through the permissionless multiclaimFor

## Question
Note that in wombat/mWomSV.sol, unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Can an attacker holding only tokens bought on market reach it via `unlock(uint256 _slotIndex)` under the attacker holds a second address so lockFor can be used across two accounts and force `getUserTotalLocked(user)` apart from `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, breaking the invariant that an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: unlock settles through the permissionless multiclaimFor)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: unlock() calls IMasterMagpie(masterMagpie).multiclaimFor(lps, vlMGPrewards, msg.sender) with an empty inner reward array, and that same entrypoint is callable by anyone against any account, so the settlement it relies on can be pre-run by an attacker at a different state. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: an exit must settle against the state at the exit, not against a checkpoint a third party fixed earlier; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds a second address so lockFor can be used across two accounts, then assert `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` end identical in both runs.
