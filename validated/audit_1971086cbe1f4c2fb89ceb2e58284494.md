### Title
Reward Over-Attribution in `ManualCompound.compound` Due to Raw Balance Instead of Pre/Post Balance Diff - (File: rewards/ManualCompound.sol)

### Summary
`ManualCompound.compound()` determines the amount of each reward token to convert, lock, or forward to the caller by reading `IERC20(_tokenAddress).balanceOf(address(this))` directly, instead of computing the difference between the balance before and after the `multiclaimOnBehalf` call. This is the exact bug class described in the external report (fee/amount computed from total post-operation balance rather than the delta of newly received tokens), applied here to reward distribution instead of fee calculation.

### Finding Description
In `compound()`, after calling `IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender)`, the function loops over configured compoundable rewards and non-compoundable rewards and uses the contract's current token balance as the "received" amount to act on: [1](#0-0) 
Specifically: [2](#0-1) [3](#0-2) 
There is no "before" balance snapshot taken prior to `multiclaimOnBehalf`, unlike the correctly-implemented pattern found elsewhere in the codebase (e.g. `WombatStaking._toMasterWomAndSendReward`, `WombatPoolHelper._deposit`, `AnkrBNBPoolHelper.depositLP`), which all snapshot balances before an external call and use the post-minus-pre delta: [4](#0-3) [5](#0-4) 

Because `compound()` uses the raw `balanceOf`, any reward token balance that is already sitting in the `ManualCompound` contract when a user calls `compound()` — whether from unconsumed dust left by a previous caller's `IConverter`/`ILocker`/`ISimpleHelper` call, or from tokens accidentally/adversarially transferred into the contract — will be swept up and attributed entirely to the current caller as if it were newly claimed on their behalf.

### Impact Explanation
This mirrors the reported bug class ("overestimated" distribution because balance-based accounting doesn't isolate the newly-received amount), but manifests as reward funds belonging to (or intended for) other participants being redirected to whichever address happens to call `compound()` next. Since `compound()` is a fully permissionless, unprivileged-wallet-reachable entry point that forwards the entire measured "receivedBalance" to `msg.sender` (via transfer, lock, or deposit), any residual balance is diverted away from its rightful destination. This is a direct fund-misattribution/diversion path stemming from the same root cause identified in the external report.

### Likelihood Explanation
`compound()` is called directly by ordinary users compounding their own claims, and no reentrancy guard or balance isolation exists to prevent pre-existing balances from being swept in. The likelihood of a non-trivial residual balance depends on external factors (dust from prior conversions, or third-party token transfers into the contract), so the size of any single incident is not guaranteed to be large, but the underlying flaw is deterministically present on every call.

### Recommendation
Snapshot each relevant token's balance in `ManualCompound.compound()` immediately before calling `IMasterMagpie.multiclaimOnBehalf`, and use the post-call minus pre-call delta (per token) as the `receivedBalance`/`rewardBalance` figure used in both the non-compoundable-reward forwarding loop and the compoundable-reward conversion/locking loop, consistent with the pattern already used in `WombatStaking._toMasterWomAndSendReward` and the `PoolHelper` contracts.

### Proof of Concept
1. Assume reward token `X` is configured as compoundable in `ManualCompound` (`rewards[i].tokenAddress == X`).
2. Some balance of `X` (e.g., left over from a prior call whose `IConverter.convertFor` or `ILocker.lockFor` did not consume the full approved amount) remains in the `ManualCompound` contract.
3. A user calls `compound()` for their own `_lps`/`_rewards`, claiming a much smaller amount of `X` via `multiclaimOnBehalf`.
4. At line 144, `receivedBalance = IERC20(X).balanceOf(address(this))` includes both the user's newly claimed amount and the pre-existing leftover balance.
5. The entire `receivedBalance` (including funds not actually claimed by this caller) is approved and forwarded to the convertor/locker/helper on behalf of `msg.sender`, diverting the leftover balance to the current caller instead of its rightful recipient.

### Citations

**File:** rewards/ManualCompound.sol (L123-149)
```text
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
        uint256 rewardTokensLength = rewards.length;        
        IMasterMagpie(masterMagpie).multiclaimOnBehalf(_lps, _rewards, msg.sender);
        // send none compoundable reward back to caller
        for(uint256 i; i < _lps.length; i++) {
            uint256 rewardLength = _rewards[i].length;
            if (rewardLength > 0) {
                for (uint j; j < rewardLength; j++) {
                    if (!compoundableRewards[_rewards[i][j]]) {
                        uint256 rewardBalance = IERC20(_rewards[i][j]).balanceOf(address(this));
                        if (rewardBalance > 0)
                            IERC20(_rewards[i][j]).safeTransfer(msg.sender, rewardBalance);
                    }
                }
            }
        }
        for (uint256 i; i< rewardTokensLength; i++) {
            address _tokenAddress = rewards[i].tokenAddress;
            address _helperAddress = rewards[i].tokenHelper;
            address _convertor = rewards[i].convertor;
            address _locker = rewards[i].locker;
            uint256 receivedBalance = IERC20(_tokenAddress).balanceOf(address(this));

            if (receivedBalance > 0) {
                if (_convertor != address(0)) {
                    IERC20(_tokenAddress).safeApprove(_convertor, receivedBalance);
                    IConverter(_convertor).convertFor(receivedBalance, _convertRatio, _minRec, msg.sender, 2);
```

**File:** wombat/WombatStaking.sol (L677-684)
```text
        uint256 womBeforeBalance = IERC20(wom).balanceOf(address(this));
        uint256[] memory beforeBalances = _rewardBeforeBalances(_lpToken);

        if(_isStake)
            _stakeToWombatMaster(_lpToken, lpAmount); // triggers harvest from wombat exchange
        else
            IMasterWombat(masterWombat).withdraw(poolInfo.pid, lpAmount); // triggers harvest from wombat exchange
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
```

**File:** wombat/WombatPoolHelper.sol (L148-152)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, msg.sender, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
```
