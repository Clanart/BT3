# Q1977: ManualCompound.compound - compoundableRewards flag is the only filter on the first loop

## Question
In rewards/ManualCompound.sol, the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under the caller passes empty inner arrays so the claim-all path runs for every pool, so that `compoundableRewards[token]` diverges from `rewards[i].tokenAddress`, the invariant that an unregistered token must be rejected rather than treated as freely transferable is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: compoundableRewards flag is the only filter on the first loop)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the first loop transfers out any caller-named token whose compoundableRewards flag is false, which is the default for every address that was never registered. Precondition: the caller passes empty inner arrays so the claim-all path runs for every pool.
- Invariant to test: an unregistered token must be rejected rather than treated as freely transferable; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller passes empty inner arrays so the claim-all path runs for every pool, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `compoundableRewards[token]` versus `rewards[i].tokenAddress` relation are unchanged by the attacker's transaction.
