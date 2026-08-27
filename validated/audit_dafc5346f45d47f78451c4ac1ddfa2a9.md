### Title
`SmartWomConvert.smartConvert` enforces a strict 1:1 `_minRec` that can be pushed to revert by pool manipulation, and the revert propagates unguarded through `WombatStaking._sendRewards` into `deposit`/`depositLP`/`withdraw`/`harvest` - (File: wombat/SmartWomConvert.sol)

### Summary
`smartConvert()` hardcodes `_minRec = _amountIn` when delegating to `_convertFor`, requiring the buyback leg (`swapExactTokensForTokens`) plus the 1:1 minted `convertAmount` to sum to at least the full input, even though the swap itself is submitted with `minOut = 0` and is thus fully exposed to slippage/manipulation on the `womMWomPool`. Because `WombatStaking._sendRewards` calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` with no `try/catch`, any revert there bubbles up through `_toMasterWomAndSendReward`, which is invoked unconditionally from `deposit`, `depositLP`, `withdraw`, and the permissionless `harvest`, blocking all of them.

### Finding Description
`smartConvert(uint256 _amountIn, uint256 _mode)` first measures a spot price via `currentRatio()` — a single `getAmountOut` call on a fixed `1e18` sample against `womMWomPool` [1](#0-0) . If that spot sample indicates a discount (`mWomToWom < buybackThreshold`), it computes `maxSwapAmount()` from the pool's `cash`/`liability` gap and derives a `convertRatio`, then calls `_convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode)` — note `_minRec` is hardcoded to the full `_amountIn`, not scaled to reflect any expected slippage [2](#0-1) .

Inside `_convertFor`, the buyback swap is executed with `minOut = 0`, meaning the swap call itself will not revert regardless of the price received; instead, the function separately enforces `convertAmount + amountRec >= _minRec` afterward and reverts with `MinRecNotMatch()` otherwise [3](#0-2) . Because the `1e18` sample used for `currentRatio()` does not reflect the actual slippage incurred when swapping the real, potentially much larger `buybackAmount` (bounded by `maxSwapAmount()`, itself derived from mutable pool `cash`/`liability`), an attacker who unbalances `womMWomPool` (e.g., via swaps that widen slippage depth while keeping the small-sample spot price just under `buybackThreshold`) can cause `amountRec` for the real-sized swap to fall below `buybackAmount`, causing the strict `_minRec` check to fail and the whole call to revert.

This function is invoked from `WombatStaking._sendRewards` with `_mode == 0` whenever a fee marked `isMWOM` and paid in `wom` is processed, with no `try/catch` protecting the call: `IConverter(smartWomConverter).smartConvert(feeAmount, 0);` [4](#0-3) . `_sendRewards` is called from `_toMasterWomAndSendReward`, which is unconditionally invoked by `deposit`, `depositLP`, `withdraw` (each of which triggers a harvest of `wom` rewards as a side effect of staking/unstaking on Wombat's `masterWombat`) and by the permissionless `harvest()` function [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) . `harvest()` has no caller restriction besides the pool being active, so an attacker can directly trigger this path, and any user's `deposit`/`depositLP`/`withdraw` will also revert as long as non-zero `wom` rewards are pending and the manipulated pool state persists.

### Impact Explanation
While the manipulated pool state persists, every `deposit`, `depositLP`, `withdraw` (whenever pending `wom` rewards exist) and `harvest` on the affected pool reverts, since the hard `MinRecNotMatch()` revert is not caught anywhere in the call chain. This blocks principal deposits and withdrawals for legitimate users, matching "Temporary freezing of funds" if the attacker sustains the adverse pool condition for a sufficient duration (≥24 hours per the target impact class).

### Likelihood Explanation
The attacker needs no privileged role — only capital to trade against the public `womMWomPool` to keep the spot sample (`currentRatio`) near/under `buybackThreshold` while the effective depth for the real buyback size yields a return below par. This is feasible for a well-capitalized attacker and is repeatable each time reward harvesting is triggered (which happens automatically on ordinary user deposits/withdrawals, or can be forced any time via the unguarded `harvest()`). The design flaw (sampling price at a fixed small size while transacting a variable, potentially much larger size, with a strict 1:1 `_minRec` and no fallback) is unconditional and does not depend on any misconfiguration.

### Recommendation
- Remove the hard `_minRec = _amountIn` requirement in `smartConvert()`; instead compute an expected minimum via `getAmountOut` on the actual `buybackAmount` (or apply a tolerance/slippage buffer) rather than requiring a full 1:1 return.
- Pass a nonzero, size-aware `minOut` into `swapExactTokensForTokens` reflecting realistic slippage instead of `0`.
- Wrap the `smartConvert` call in `WombatStaking._sendRewards` in a `try/catch`, falling back to plain `IMWom(mWom).deposit` (or skipping the buyback and queuing raw `wom`) on failure, so that a reverting buyback never blocks `deposit`/`depositLP`/`withdraw`/`harvest`.

### Proof of Concept
Foundry fork test outline:
1. Fork BSC at a block where `womMWomPool`, `WombatStaking`, and `SmartWomConvert` are deployed and `smartWomConverter` is set with an active `isMWOM` fee on `wom`.
2. Attacker account: perform large swaps against `womMWomPool` to push actual executable slippage for a realistic `buybackAmount` below the discount implied by the small `1e18` sample used in `currentRatio()`, while keeping `currentRatio() < buybackThreshold`.
3. Victim account: call the pool's user-facing deposit function that routes to `WombatStaking.deposit`/`depositLP` with pending `wom` rewards outstanding.
4. Assert: the victim's transaction reverts with `MinRecNotMatch()` (propagated from `SmartWomConvert._convertFor` through `_sendRewards`/`_toMasterWomAndSendReward`), and that this holds while the attacker maintains the manipulated pool state, verifying `amountRec` from `swapExactTokensForTokens` falls below `convertAmount`/`buybackAmount` parity.
5. Repeat calling `harvest()` directly as the attacker (no role needed) to show the same revert is directly attacker-triggerable without waiting for a victim transaction.

### Citations

**File:** wombat/SmartWomConvert.sol (L107-117)
```text
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

**File:** wombat/SmartWomConvert.sol (L186-205)
```text
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

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```

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

**File:** wombat/WombatStaking.sol (L331-335)
```text
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
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
