# Q5995: MasterMagpie.multiclaim - lpSupply inflation by direct token donation

## Question
rewards/MasterMagpie.sol - _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Can an unprivileged attacker controlling the full _stakingTokens array, including duplicates and unregistered addresses, under the attacker splits the action across two transactions in the same block with a flash-loaned staking token, exploit this through `multiclaim(address[] _stakingTokens)` to break the reconciliation between `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` and `block.timestamp` and the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaim(address[] _stakingTokens)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaim(address[] _stakingTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the full _stakingTokens array, including duplicates and unregistered addresses
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the attacker splits the action across two transactions in the same block with a flash-loaned staking token.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker splits the action across two transactions in the same block with a flash-loaned staking token, call `multiclaim(address[] _stakingTokens)`, and assert `tokenToPoolInfo[_stakingToken].lastRewardTimestamp` equals `block.timestamp` and that no account can withdraw more than it put in.
