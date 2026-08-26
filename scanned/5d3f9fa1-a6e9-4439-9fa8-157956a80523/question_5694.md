# Q5694: MasterMagpie.multiclaimSpec - lpSupply inflation by direct token donation

## Question
In rewards/MasterMagpie.sol, _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Can an unprivileged attacker reach this through `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` while the contract is paused so only emergencyWithdraw is reachable, and drive `userInfo[_stakingToken][user].rewardDebt` out of agreement with `tokenToPoolInfo[_stakingToken].accMGPPerShare` - breaking the invariant that MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/MasterMagpie.sol -> `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` (mechanism: lpSupply inflation by direct token donation)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: both outer and inner arrays, so every reward-token address and its order
- Exploit idea: _calLpSupply() returns IERC20(_stakingToken).balanceOf(address(this)) for every non-vlMGP/non-mWomSV pool, so a raw ERC20 transfer of the receipt token straight to MasterMagpie inflates the accMGPPerShare denominator without crediting any UserInfo.amount. Precondition: the contract is paused so only emergencyWithdraw is reachable.
- Invariant to test: MGP emitted over an interval must be fully distributable to the sum of UserInfo.amount, and accMGPPerShare must only ever be divided by staked-and-credited supply; concretely, `userInfo[_stakingToken][user].rewardDebt` must stay reconciled with `tokenToPoolInfo[_stakingToken].accMGPPerShare`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `multiclaimSpec(address[] _stakingTokens, address[][] _rewardTokens)` sequence atomically under the contract is paused so only emergencyWithdraw is reachable, asserting at the end that `userInfo[_stakingToken][user].rewardDebt` still equals `tokenToPoolInfo[_stakingToken].accMGPPerShare` and the PoC's balance delta is non-positive.
