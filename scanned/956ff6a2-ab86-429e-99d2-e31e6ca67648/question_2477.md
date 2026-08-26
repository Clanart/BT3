# Q2477: WombatStaking.convertWOM - veWOM lock commits pooled WOM for lockDays with no user opt-out

## Question
wombat/WombatStaking.sol - convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Can an unprivileged attacker controlling _amount, with no upper bound and no relation to who supplied the WOM, under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, exploit this through `convertWOM(uint256 _amount)` to break the reconciliation between `totalAccumulated in mWOM` and `veWom balance of WombatStaking` and the invariant that the backing of a liquid wrapper must remain redeemable under terms its holders accepted, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatStaking.sol -> `convertWOM(uint256 _amount)` (mechanism: veWOM lock commits pooled WOM for lockDays with no user opt-out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertWOM(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, with no upper bound and no relation to who supplied the WOM
- Exploit idea: convertWOM() locks for lockDays with no per-depositor accounting, so an mWOM holder's underlying WOM sits inside a veWOM lock they never agreed to and cannot address individually. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: the backing of a liquid wrapper must remain redeemable under terms its holders accepted; concretely, `totalAccumulated in mWOM` must stay reconciled with `veWom balance of WombatStaking`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, then assert `totalAccumulated in mWOM` and `veWom balance of WombatStaking` end identical in both runs.
