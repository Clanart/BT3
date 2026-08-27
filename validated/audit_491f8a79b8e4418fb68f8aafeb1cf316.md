### Title
Deposit/withdraw revert due to shared `_toMasterWomAndSendReward` fee/smartConvert path can be griefed via wom/mWom pool price manipulation, freezing principal - (File: wombat/WombatStaking.sol)

### Summary
`WombatPoolHelper.deposit`, `depositLP`, and `withdraw` all route through `WombatStaking._toMasterWomAndSendReward`, which unconditionally harvests WOM rewards and runs them through the fee distribution loop, including an optional `smartConvert` leg that swaps a portion of WOM for mWOM via the external `womMWomPool`. Because `SmartWomConvert.smartConvert` enforces `convertAmount + amountRec >= _amountIn` (using the full input amount as `_minRec`), an unprivileged attacker who has just moved the wom/mWom pool price can cause this check to fail, reverting the entire harvest call and, with it, every deposit/withdraw for the pool.

### Finding Description
`WombatPoolHelper.deposit` calls `WombatStaking.deposit`, which after minting Wombat LP calls `_toMasterWomAndSendReward(_lpAddress, lpReceived, true)` [1](#0-0) . `withdraw` and `depositLP` do the same before/around the actual principal movement [2](#0-1) .

`_toMasterWomAndSendReward` stakes/unstakes to `masterWombat` (which auto-harvests pending WOM to the contract) and then calls `_sendRewards` for the harvested WOM [3](#0-2) .

`_sendRewards` iterates `feeInfos`; for any active fee marked `isMWOM` on the WOM reward, it calls `smartWomConverter.smartConvert(feeAmount, 0)` [4](#0-3) .

`SmartWomConvert.smartConvert` computes `currentRatio()` (spot price for a notional 1e18 mWOM) and, if below `buybackThreshold`, buys mWOM with a portion of the WOM fee via `router.swapExactTokensForTokens(..., 0, ...)` (zero minimum out on the swap itself), then enforces `convertAmount + amountRec >= _amountIn` via `_minRec = _amountIn`, reverting with `MinRecNotMatch` otherwise [5](#0-4) [6](#0-5) .

`currentRatio()` is a spot-price check on a small notional, while the actual swap uses the full `buybackAmount` computed from the harvested fee. An attacker who moves the wom/mWom pool immediately before the victim/attacker's own deposit or withdraw call can (a) push `currentRatio()` below `buybackThreshold` to force the buyback branch, and/or (b) leave the pool thin/imbalanced so that the real swap of `buybackAmount` experiences price impact worse than the discount implied by the spot price, making `amountRec < buybackAmount` and tripping `MinRecNotMatch`. Because this check sits inside `_sendRewards`, called unconditionally from `_toMasterWomAndSendReward`, called unconditionally from every `deposit`, `depositLP`, `withdraw`, and `harvest` in `WombatStaking.sol`, the revert propagates all the way up and blocks principal movement for the entire pool, not just the wom/mWom pool itself (since the `isMWOM` fee applies to any WOM reward regardless of which asset pool emitted it).

Existing modifiers (`nonReentrant`, `whenNotPaused`, `_onlyActivePoolHelper`) do not guard against this: they don't decouple the optional reward-conversion leg from principal deposit/withdraw logic.

### Impact Explanation
This is a real coupling issue: principal deposits and withdrawals for a pool depend on an external AMM price and on an optional reward-conversion leg (`smartConvert`) succeeding. If an attacker forces `MinRecNotMatch` to revert reliably (e.g., by keeping the wom/mWom pool imbalanced or thin), all users are blocked from depositing or withdrawing from the affected Wombat pool(s) until the price recovers or an admin intervenes (e.g., disabling the `isMWOM` fee, unsetting `smartWomConverter`, or pausing). This matches a temporary freezing-of-funds impact, since user principal already deposited becomes unwithdrawable while the condition persists.

Note: this does not create a state where `_minimumLiquidity` supplied by the caller diverges from the LP actually minted, because Solidity reverts are atomic — a failing `_sendRewards` call reverts the whole transaction, so no partial minting occurs. The actual, verifiable defect is the DoS/liveness coupling between the optional reward-conversion leg and principal deposit/withdraw availability, not an accounting/slippage-bypass divergence.

### Likelihood Explanation
Requires: (1) a `Fees` entry configured with `isMWOM = true` and `isActive = true`, and `smartWomConverter` set (normal, expected production configuration, not attacker-introduced misconfiguration) [7](#0-6) ; (2) sufficient capital/liquidity conditions in the `womMWomPool` for the attacker to move the spot ratio and/or degrade swap execution below the harvested fee amount. Attacker needs no privileged role — anyone can trade against the public Wombat wom/mWom pool and then call `deposit`/`withdraw`/`harvest` on `WombatPoolHelper`. Feasibility depends heavily on the pool's depth and current fee accrual size at attack time, so likelihood is conditional rather than trivially guaranteed on every block.

### Recommendation
Decouple principal deposit/withdraw from the optional reward-harvest/fee/convert path: wrap the harvest-and-fee-distribution call (`_toMasterWomAndSendReward` → `_sendRewards` → `smartConvert`) in a try/catch (or move it to a separate, independently callable/retryable function) so that a revert in the reward-conversion leg cannot block principal deposit/withdraw. Additionally, consider bounding `smartConvert`'s swap size or making the `_minRec` check advisory (e.g., skip the mWOM conversion and fall back to plain WOM transfer/queue on failure) rather than reverting.

### Proof of Concept
Hardhat test plan:
1. Deploy `WombatStaking`, `WombatPoolHelper`, `SmartWomConvert`, mock `IWombatRouter`/`womMWomPool` (or fork BSC mainnet with real Wombat wom/mWom pool), and `mWOM`.
2. Configure a `Fees` entry with `isMWOM = true`, `isActive = true`, and set `smartWomConverter`.
3. Seed the pool with WOM rewards pending in `masterWombat` for the target lpToken so that a `deposit`/`withdraw` call will harvest a non-trivial WOM amount.
4. As the attacker (unprivileged EOA), perform a large swap in the wom/mWom pool to move `currentRatio()` below `buybackThreshold` and/or thin out pool liquidity.
5. Call `WombatPoolHelper.deposit(_amount, _minimumLiquidity)` (or `withdraw`) as any user; assert the transaction reverts with `MinRecNotMatch` originating from `SmartWomConvert._convertFor`.
6. Repeat without the attacker's pre-trade to show the same deposit/withdraw succeeds normally, confirming the attacker-controlled precondition is the differentiator.
7. Assert that while the condition persists, all `deposit`/`depositLP`/`withdraw`/`harvest` calls on `WombatPoolHelper` for pools sharing the WOM reward revert, demonstrating pool-wide freezing rather than a single-user effect.

### Citations

**File:** wombat/WombatStaking.sol (L256-269)
```text
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
```

**File:** wombat/WombatStaking.sol (L272-321)
```text
    function depositLP(
        address _lpAddress,
        uint256 _lpAmount,
        address _for
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];

        // Transfer lp to this contract and stake it to wombat
        IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);

        _toMasterWomAndSendReward(_lpAddress, _lpAmount, true); // triggers harvest from wombat exchange
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount);

        emit NewLPDeposit(_for, poolInfo.lpAddress, _lpAmount, poolInfo.receiptToken, _lpAmount);
    }

    /// @notice withdraw from a wombat Pool. Note!!! pool helper has to burn receipt token!
    /// @dev Only a PoolHelper can call this function
    /// @param _lpToken the address of the wombat pool lp token
    /// @param _liquidity wombat pool liquidity
    /// @param _minAmount The minimal amount the user accepts because of slippage
    /// @param _sender the address of the user
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

**File:** wombat/WombatStaking.sol (L671-696)
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

        for (uint256 i; i < bonusTokensLength; i++) {
            uint256 bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i];
            if (bonusBalanceDiff > 0) {
                _sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff);
            }
        }

        emit WomHarvested(womRewards);

    }
```

**File:** wombat/WombatStaking.sol (L729-753)
```text
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
                        } else {
                            IERC20(wom).safeApprove(mWom, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IMWom(mWom).deposit(feeAmount);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        }
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

**File:** wombat/SmartWomConvert.sol (L199-205)
```text
        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```
