# Q3039: ManualCompound.compound - locker branch sends the whole balance to msg.sender

## Question
In rewards/ManualCompound.sol, when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under no convertor, locker or helper is configured for one of the registered rewards, so that `IERC20(_rewards[i][j]).balanceOf(address(this))` diverges from `the amount this caller actually claimed through multiclaimOnBehalf`, the invariant that a locking branch must lock only the caller's own earned amount is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: locker branch sends the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Precondition: no convertor, locker or helper is configured for one of the registered rewards.
- Invariant to test: a locking branch must lock only the caller's own earned amount; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange no convertor, locker or helper is configured for one of the registered rewards, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `IERC20(_rewards[i][j]).balanceOf(address(this))` equals `the amount this caller actually claimed through multiclaimOnBehalf` and that no account can withdraw more than it put in.
