# Q3057: ManualCompound.compound - helper branch deposits the whole balance for msg.sender

## Question
In rewards/ManualCompound.sol, the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Can an unprivileged attacker reach this through `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` while no convertor, locker or helper is configured for one of the registered rewards, and drive `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` out of agreement with `the caller's own share of that reward token` - breaking the invariant that a deposit branch must credit only the caller's own earned amount - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: helper branch deposits the whole balance for msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender) branch credits the full contract balance of that token to the caller in MasterMagpie. Precondition: no convertor, locker or helper is configured for one of the registered rewards.
- Invariant to test: a deposit branch must credit only the caller's own earned amount; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under no convertor, locker or helper is configured for one of the registered rewards, asserting on every row that a deposit branch must credit only the caller's own earned amount.
