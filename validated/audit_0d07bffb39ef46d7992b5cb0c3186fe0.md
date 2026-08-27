### Title
Reward-fee conversion reverts on zero amount, permanently DoS'ing `WombatStaking` deposit/withdraw/harvest - (File: wombat/WombatStaking.sol, wombat/SmartWomConvert.sol)

### Summary
`WombatStaking::deposit`, `withdraw`, `depositLP`, and `harvest` all route harvested WOM rewards through `_toMasterWomAndSendReward` → `_sendRewards`, which computes a fee slice of the harvested reward and forwards it to `SmartWomConvert::smartConvert`. That function unconditionally reverts if the amount passed to it is `0`. Because the fee slice is a percentage of the harvested amount, any combination of a small/legitimate fee value and a small harvested reward amount rounds the fee down to zero, causing the whole fee-forwarding call — and therefore the entire deposit/withdraw/harvest transaction — to revert. This mirrors the report's root cause: a "nothing to swap"/zero-amount guard placed in a downstream fee-processing call that is unconditionally invoked from core user flows.

### Finding Description
`_sendRewards` computes a per-fee-slot amount from the harvested reward and, for the WOM-to-mWOM fee slot, forwards it to the configured converter: [1](#0-0) 

`SmartWomConvert::smartConvert` reverts if it is called with a zero amount: [2](#0-1) 

`_sendRewards` itself only guards against the *total* reward amount being zero (`if (_amount == 0) return;`), but does not guard the *per-fee* amount computed inside the loop: [3](#0-2) 

Because `feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR` uses integer division, any harvested WOM amount smaller than `DENOMINATOR / feeInfo.value` rounds `feeAmount` to `0` even though `originalRewardAmount > 0`. When `smartWomConverter` is set (a normal, intended admin configuration, not a malicious one) and the fee slot is `isMWOM && rewardToken == wom` and `isActive`, this zero `feeAmount` is passed straight into `smartConvert`, which reverts with `MustNoBeZero()`.

This call is reached unconditionally from unprivileged, user-triggered paths: [4](#0-3) [5](#0-4) [6](#0-5) 

`_toMasterWomAndSendReward` triggers harvesting from the underlying Wombat pool and immediately calls `_sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards)`: [7](#0-6) 

Any regular wallet calling `deposit`, `withdraw`, `depositLP`, or `harvest` (directly, or indirectly through `WombatPoolHelper`/`WombatPoolHelperV2`, which are plain pass-through wrappers callable by any user) triggers this code path: [8](#0-7) 

### Impact Explanation
Once a pool's accrued/harvested WOM reward for a given transaction happens to be small enough that the configured fee percentage rounds to zero, every subsequent `deposit`, `withdraw`, `depositLP`, and `harvest` call for that pool reverts, because `_toMasterWomAndSendReward` is called unconditionally on every such action and always attempts to re-harvest and re-process fees. This blocks ordinary users from withdrawing their already-staked LP funds through the intended path, constituting a freezing of user funds (deposits become stuck) that can persist indefinitely (well beyond 24 hours) until an admin removes/adjusts the offending fee configuration or disables `isPoolFeeFree`.

### Likelihood Explanation
This does not require a malicious admin: it is triggered whenever the protocol owner uses a normal, intuitive small `isMWOM` fee value (e.g., a few basis points) together with the `smartWomConverter` — both are supported, documented admin actions (`setFee`-style calls, `setBribe`, etc. are not needed; the WOM fee list and `smartWomConverter` are configured via `WombatStaking`'s admin functions). Whenever a pool's per-transaction WOM harvest is small relative to `DENOMINATOR/feeInfo.value` (e.g., low-TVL pools, or pools between reward accrual events), the fee rounds to zero and the revert fires. This can also be forced by any user by triggering `harvest()` repeatedly in quick succession so each individual harvest slice is tiny.

### Recommendation
Guard the per-fee `feeAmount` before calling `smartConvert` (and any other downstream call with a zero-amount revert), e.g.:
```solidity
if (feeInfo.isMWOM && rewardToken == wom && feeAmount > 0) {
    ...
    IConverter(smartWomConverter).smartConvert(feeAmount, 0);
    ...
}
```
Alternatively, make `SmartWomConvert::smartConvert` a no-op (return 0) instead of reverting when `_amountIn == 0`.

### Proof of Concept
1. Admin configures a WOM fee slot with `isMWOM = true`, `isActive = true`, and a small `value` (e.g., `value = 1` out of `DENOMINATOR = 10000`, i.e., 0.01%), and sets `smartWomConverter`.
2. A user calls `WombatPoolHelper.harvest()` (or `deposit`/`withdraw`) for a pool where the harvested WOM reward for that call is less than `10000` wei (i.e., less than `DENOMINATOR / value`).
3. Inside `_sendRewards`, `feeAmount = (originalRewardAmount * 1) / 10000 == 0`.
4. `IConverter(smartWomConverter).smartConvert(0, 0)` is called, which reverts with `MustNoBeZero()` per `wombat/SmartWomConvert.sol` lines 133-134.
5. The revert propagates up through `_sendRewards` → `_toMasterWomAndSendReward` → `deposit`/`withdraw`/`harvest`, reverting the entire user transaction and blocking that pool's deposit/withdraw/harvest functionality until the fee configuration is changed by the admin.

### Citations

**File:** wombat/WombatStaking.sol (L241-270)
```text
    /// @param _from the address to transfer from
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

**File:** wombat/WombatStaking.sol (L671-685)
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

**File:** wombat/WombatStaking.sol (L720-745)
```text
    function _sendRewards(
        address _lpToken,
        address _rewardToken,
        address _rewarder,
        uint256 _amount
    ) internal {
        if (_amount == 0) return;
        uint256 originalRewardAmount = _amount;

        if (!isPoolFeeFree[_lpToken]) {
            for (uint256 i = 0; i < feeInfos.length; i++) {
                Fees storage feeInfo = feeInfos[i];

                if (feeInfo.isActive) {
                    address rewardToken = _rewardToken;
                    uint256 feeAmount = (originalRewardAmount * feeInfo.value) / DENOMINATOR;
                    _amount -= feeAmount;
                    uint256 feeTosend = feeAmount;

                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
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

**File:** wombat/WombatPoolHelper.sol (L96-144)
```text
    /// @notice deposit stables in wombat pool, autostake in master magpie    
    /// @param _amount the amount of stables to deposit
    function deposit(uint256 _amount, uint256 _minimumLiquidity) external override {
        _deposit(_amount, _minimumLiquidity, msg.sender);
    }

    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }

    function depositNative(uint256 _minimumLiquidity) external payable {
        if(!isNative) revert NotNativeToken();
        // Dose need to limit the amount must > 0?

        // Swap the BNB to wBNB
        _wrapNative();
        // depsoit wBNB to the pool
        IWNative(depositToken).approve(wombatStaking, msg.value);
        _deposit(msg.value, _minimumLiquidity, address(this));
        IWNative(depositToken).approve(wombatStaking, 0);
    }

    /// @notice withdraw stables from wombat pool, auto unstake from master Magpie
    /// @param _liquidity the amount of liquidity to withdraw
    function withdraw(uint256 _liquidity, uint256 _minAmount) external override {
        // we have to withdraw from wombat exchange to harvest reward to base rewarder
        IWombatStaking(wombatStaking).withdraw(
            lpToken,
            _liquidity,
            _minAmount,
            msg.sender
        );
        // then we unstake from master wombat to trigger reward distribution from basereward
        _unstake(_liquidity, msg.sender);
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }

    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }
```
