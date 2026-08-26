# Q1745: ManualCompound.compound - stranded dust from a previous compound is claimable by anyone

## Question
In rewards/ManualCompound.sol, any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Starting from a state where the caller passes empty inner arrays so the claim-all path runs for every pool, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `compoundableRewards[token]` inconsistent with `rewards[i].tokenAddress`, violating the invariant that residual value must be attributed to its owner rather than left claimable by the next caller and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: stranded dust from a previous compound is claimable by anyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: any token that a previous compound left behind through rounding, a partial convert or an under-consuming helper stays on the contract until the next caller sweeps the full balance. Precondition: the caller passes empty inner arrays so the claim-all path runs for every pool.
- Invariant to test: residual value must be attributed to its owner rather than left claimable by the next caller; concretely, `compoundableRewards[token]` must stay reconciled with `rewards[i].tokenAddress`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller passes empty inner arrays so the claim-all path runs for every pool, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `compoundableRewards[token]` equals `rewards[i].tokenAddress` and that no account can withdraw more than it put in.
