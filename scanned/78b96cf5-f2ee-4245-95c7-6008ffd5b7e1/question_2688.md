# Q2688: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
wombat/mWomSV.sol - VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Can an unprivileged attacker controlling _amountToCoolDown and the timestamps written into the slot, under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, call `startUnlock(uint256 _amountToCoolDown)`, and assert `getUserTotalLocked(user)` equals `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and that no account can withdraw more than it put in.
