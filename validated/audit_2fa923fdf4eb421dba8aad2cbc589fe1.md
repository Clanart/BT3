### Title
Permanent DoS of Wombat deposits/withdrawals/harvests via non-zero-to-non-zero `safeApprove` to `smartWomConverter` - (File: wombat/WombatStaking.sol)

### Summary
`WombatStaking._sendRewards` unconditionally re-approves the `wom` token to `smartWomConverter` on every reward distribution without first resetting the allowance to zero. Because `SafeERC20.safeApprove` reverts on a non-zero→non-zero allowance change, a single failed or partially-consumed call to `smartWomConverter.smartConvert` leaves a stale non-zero allowance that will make every subsequent `deposit`, `withdraw`, `harvest`, and `vote` call on that pool permanently revert — freezing user funds with no recovery path, analogous to the `activeIncentive`/`AlgebraVirtualPool` coupling issue in the referenced report where an auxiliary/incentive-related external call, if it fails, blocks the core user-facing operation.

### Finding Description
`_toMasterWomAndSendReward` is invoked on every `deposit`, `withdraw`, and `harvest` call in `WombatStaking.sol` [1](#0-0) , and it always routes reward tokens through `_sendRewards`:

```
IERC20(wom).safeApprove(smartWomConverter, feeAmount);
uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
IConverter(smartWomConverter).smartConvert(feeAmount, 0);
``` [2](#0-1) 

There is no `safeApprove(smartWomConverter, 0)` reset prior to this call, unlike the reward-side approve two lines later that explicitly resets first (`IERC20(_rewardToken).safeApprove(_rewarder, 0); ... safeApprove(_rewarder, _amount);`) [3](#0-2) .

OpenZeppelin's `safeApprove` reverts unless the current allowance is `0` or the new value is `0`. If `smartConvert` ever reverts *after* the `safeApprove` line executes but *before* it fully consumes the granted allowance via `transferFrom` inside `_convertFor` [4](#0-3) , or if the whole `smartConvert` call reverts for any reason at all while the approval is already set, the outstanding non-zero `wom` allowance to `smartWomConverter` is never cleared. `smartConvert` itself calls external Wombat Router/Asset views (`currentRatio`, `maxSwapAmount`) and a router swap that can revert under normal market conditions (e.g., insufficient liquidity, price-impact limits) [5](#0-4) [6](#0-5) .

Once this happens, every future call into `_sendRewards` for that pool re-attempts `IERC20(wom).safeApprove(smartWomConverter, feeAmount)` on top of the already non-zero leftover allowance, and reverts unconditionally. Since `_sendRewards` is on the mandatory execution path of `deposit`, `withdraw`, and `harvest` [7](#0-6) [8](#0-7) [9](#0-8) , this permanently blocks core fund movement for the entire pool — not just a temporary block until the external issue resolves (as in the referenced `activeIncentive` report), but an unrecoverable state, since nothing in the contract clears the stale allowance.

### Impact Explanation
Any ordinary user's `deposit`, `withdraw`, or `harvest` call (reachable through the pool helper contracts such as `WombatPoolHelper`/`WombatPoolHelperV2`/`AnkrBNBPoolHelper`) can trigger the reward-forwarding path. Once the stale allowance condition is hit, **all users' LP funds in that Wombat pool become permanently stuck** — withdrawals as well as deposits fail, satisfying the "permanent freezing of funds" criterion.

### Likelihood Explanation
The trigger only requires `smartConvert` to fail once while WOM has already been approved (e.g., the underlying Wombat Router reverts due to normal slippage/liquidity conditions, or reentrancy/pause states inside the router). This can happen during routine operation since `smartConvert`'s internal swap depends on live pool liquidity conditions, and is reachable from any user's deposit/withdraw/harvest call, not from a privileged actor.

### Recommendation
In `_sendRewards`, reset the `wom` allowance to `smartWomConverter` to zero before approving a new amount, mirroring the pattern already used for the rewarder approval two lines below:
```solidity
IERC20(wom).safeApprove(smartWomConverter, 0);
IERC20(wom).safeApprove(smartWomConverter, feeAmount);
```
Additionally, consider migrating to `forceApprove`/`safeIncreaseAllowance` semantics and wrapping the external `smartConvert` call so a revert there cannot leave the staking contract's core deposit/withdraw/harvest paths permanently bricked.

### Proof of Concept
1. `smartWomConverter` is configured and `isPoolFeeFree[_lpToken]` is false, with an `isMWOM` fee active.
2. A user calls `deposit`/`withdraw`/`harvest` on `WombatStaking`, triggering `_sendRewards` → `IERC20(wom).safeApprove(smartWomConverter, feeAmount)` followed by `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` [2](#0-1) .
3. `smartConvert` reverts due to a transient condition in the underlying Wombat router/asset (e.g., `getAmountOut`/`swapExactTokensForTokens` failing) [10](#0-9) , leaving the `wom` allowance to `smartWomConverter` non-zero (the earlier `safeApprove` already succeeded and set it).
4. Any subsequent call to `deposit`/`withdraw`/`harvest` re-executes `_sendRewards`, which calls `safeApprove(smartWomConverter, feeAmount)` again on top of the still non-zero allowance from step 3, causing OpenZeppelin's `SafeERC20: approve from non-zero to non-zero allowance` revert.
5. Because no code path ever resets this allowance, every future deposit/withdraw/harvest for the pool reverts permanently, locking all depositors' funds.

### Citations

**File:** wombat/WombatStaking.sol (L242-270)
```text
    function deposit(
        address _lpAddress,
        uint256 _amount,
        uint256 _minimumLiquidity,
        address _for,
        address _from
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];
        address depositToken = poolInfo.depositToken;
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
    }
```

**File:** wombat/WombatStaking.sol (L295-321)
```text
    function withdraw(
        address _lpToken,
        uint256 _liquidity,
        uint256 _minAmount,
        address _sender
    ) nonReentrant whenNotPaused _onlyPoolHelper(_lpToken) external {
        Pool storage poolInfo = pools[_lpToken];

        IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity);
        _toMasterWomAndSendReward(_lpToken, _liquidity, false);

        uint256 beforeWithdraw = IERC20(poolInfo.depositToken).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).withdraw(
            poolInfo.depositToken,
            _liquidity,
            _minAmount,
            address(this),
            block.timestamp
        );

        IERC20(poolInfo.depositToken).safeTransfer(
            _sender,
            IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
        );

        emit NewWithdraw(_sender, poolInfo.depositToken, _liquidity);
    }
```

**File:** wombat/WombatStaking.sol (L329-335)
```text
    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L671-686)
```text
    function _toMasterWomAndSendReward(address _lpToken, uint256 lpAmount, bool _isStake) internal {
        Pool storage poolInfo = pools[_lpToken];

        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;

        uint256 womBeforeBalance = IERC20(wom).balanceOf(address(this));
        uint256[] memory beforeBalances = _rewardBeforeBalances(_lpToken);

        if(_isStake)
            _stakeToWombatMaster(_lpToken, lpAmount); // triggers harvest from wombat exchange
        else
            IMasterWombat(masterWombat).withdraw(poolInfo.pid, lpAmount); // triggers harvest from wombat exchange
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
        _sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards);

```

**File:** wombat/WombatStaking.sol (L739-745)
```text
                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
```

**File:** wombat/WombatStaking.sol (L767-769)
```text
        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
```

**File:** wombat/SmartWomConvert.sol (L98-117)
```text
    function maxSwapAmount() public view returns (uint256) {
        uint256 womCash = IAsset(womAsset).cash();
        uint256 womLiability = IAsset(womAsset).liability();
        if (womCash >= womLiability)
            return 0;

        return (womLiability - womCash) * ratio / DENOMINATOR;
    }

    function currentRatio() public view returns (uint256) {
        address[] memory tokenPath = new address[](2);
        tokenPath[0] = mWom;
        tokenPath[1] = wom;
        
        address[] memory poolPath = new address[](1);
        poolPath[0] = womMWomPool;
    
        (uint256 amountOut, ) = IWombatRouter(router).getAmountOut(tokenPath, poolPath, 1e18);
        return amountOut * DENOMINATOR / 1e18;
    }
```

**File:** wombat/SmartWomConvert.sol (L133-147)
```text
    function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        if (_amountIn == 0) revert MustNoBeZero();

        uint256 convertRatio = DENOMINATOR;
        uint256 mWomToWom = currentRatio();

        if (mWomToWom < buybackThreshold) {
            uint256 maxSwap = maxSwapAmount();
            uint256 amountToSwap = _amountIn > maxSwap ? maxSwap : _amountIn;
            uint256 convertAmount = _amountIn - amountToSwap;
            convertRatio = convertAmount * DENOMINATOR / _amountIn;
        }

        return _convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode);
    }
```

**File:** wombat/SmartWomConvert.sol (L175-197)
```text
    function _convertFor(uint256 _amount, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        internal returns (uint256 obtainedmWomAmount) {

        if (_convertRatio > DENOMINATOR)
            revert IncorrectRatio();

        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
        uint256 amountRec = 0;

        if (buybackAmount > 0) {
            address[] memory tokenPath = new address[](2);
            tokenPath[0] = wom;
            tokenPath[1] = mWom;
            address[] memory poolPath = new address[](1);
            poolPath[0] = womMWomPool;
        
            IERC20(wom).safeApprove(router, buybackAmount);
            amountRec = IWombatRouter(router).swapExactTokensForTokens(
                tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
            );
        }
```
