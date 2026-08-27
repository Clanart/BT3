Based on my research, I found a concrete analog of this bug class within the allowed scope (`WombatStaking`/`SmartWomConvert`), specifically in the "swap-check compares the wrong/incorrect variable, causing legitimate, in-scope operations to revert" pattern from the Uniswap `beforeSwap()` report.

### Title
`SmartWomConvert::smartConvert()` enforces an incorrect minimum-receive check that can revert legitimate WOM→mWOM buyback conversions, causing WombatStaking reward harvesting (and the deposits/withdrawals that trigger it) to permanently fail - (File: `wombat/SmartWomConvert.sol`)

### Summary
`SmartWomConvert.smartConvert()` is invoked from `WombatStaking._sendRewards()` on every harvest that routes WOM protocol fees into mWOM [1](#0-0) . When it decides to perform a partial buyback swap (WOM→mWOM through the pool) because `mWomToWom < buybackThreshold`, it forwards a hard-coded `_minRec` equal to the full input amount (`_amountIn`) instead of an actual expected/estimated output, to the internal conversion function that reverts if the received amount is below that value [2](#0-1) .

### Finding Description
In `smartConvert()`:
```solidity
function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
    ...
    return _convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode);
}
```
`_amountIn` is passed both as the amount to convert **and** as `_minRec` [3](#0-2) . Inside `_convertFor()`, part of the input (`buybackAmount`) is routed through an actual AMM swap (`wom->mWom`) subject to pool fees and slippage, while the rest (`convertAmount`) is minted 1:1 via `IMWom(mWom).deposit()` [4](#0-3) . The function then checks the combined output against the hard-coded `_minRec = _amountIn`:
```solidity
if (convertAmount + amountRec < _minRec)
    revert MinRecNotMatch();
``` [5](#0-4) 

This is the same bug class as the referenced report: a critical guard is checked against the **wrong reference variable**. Here, the check assumes the swap will always return at least a strict 1:1 output (`_amountIn`), rather than checking against a properly computed expected minimum output for the actual swap portion (e.g., using `estimateTotalConversion()`'s logic, which correctly derives an `amountOut` from the router quote) [6](#0-5) . Because `buybackAmount` is exchanged through a real pool (`womMWomPool`) that charges swap fees, `amountRec` for that portion can easily fall short of a strict 1:1 return even in scenarios that are otherwise legitimate — particularly near the `buybackThreshold` boundary where the price premium for buying discounted mWOM is small and is consumed by the pool's swap fee, or when `maxSwapAmount()` caps the swap size such that the theoretical price benefit doesn't cover the fee on the executed size.

### Impact Explanation
`smartConvert()` is called unconditionally by `WombatStaking._sendRewards()` for every WOM reward fee marked `isMWOM` whenever `smartWomConverter != address(0)` [7](#0-6) . `_sendRewards()` itself is called from `_toMasterWomAndSendReward()`, which is triggered by ordinary user actions: `deposit()`, `withdraw()`, `depositLP()`, and `harvest()` on `WombatStaking` [8](#0-7) . Because Solidity calls are atomic, a revert inside `smartConvert()` bubbles up and reverts the entire user-initiated deposit/withdraw/harvest transaction. If the WOM/mWOM pool ratio remains below `buybackThreshold` (a normal market condition that can persist for a long, indefinite time), every subsequent deposit, withdrawal, or harvest attempt that routes any WOM fee is blocked, freezing user LP deposits/withdrawals and unclaimed yield distribution for as long as the condition persists — satisfying the "24-hour-plus freeze of funds/yield" impact bar.

### Likelihood Explanation
This does not require any privileged role — it is triggered purely by normal user activity (deposit/withdraw/harvest) combined with an ordinary, expected market state (mWOM trading below peg relative to WOM, which is precisely the condition the buyback logic is designed to react to). No malicious admin, oracle manipulation, or external-protocol assumption is needed.

### Recommendation
In `_convertFor()`/`smartConvert()`, do not use the raw `_amountIn` as `_minRec` for a swap-based conversion. Compute the actual expected minimum output for the buyback portion (e.g., via the router's `getAmountOut` as already done in `estimateTotalConversion()`) and check the swap output (`amountRec`) against that computed value, and/or check `convertAmount + amountRec` against a realistic minimum output including this expected component, rather than against `_amountIn`.

### Proof of Concept
Given: `mWomToWom` (from `currentRatio()`) is below `buybackThreshold` (e.g., 8990 vs threshold 9000), the pool has a small nonzero swap fee, and `maxSwapAmount()` limits `amountToSwap` to a small fraction of `_amountIn`:
1. `_sendRewards()` calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` as part of a normal `WombatStaking.deposit()`/`withdraw()`/`harvest()` call [9](#0-8) .
2. `smartConvert()` computes `convertRatio` and calls `_convertFor(_amountIn, convertRatio, _amountIn, msg.sender, 0)` [10](#0-9) .
3. `_convertFor()` swaps `buybackAmount` through `womMWomPool`; due to the pool's swap fee, `amountRec` is slightly less than `buybackAmount`, so `convertAmount + amountRec < _amountIn`.
4. `_convertFor()` reverts with `MinRecNotMatch()` [5](#0-4) , which reverts the entire outer `WombatStaking` deposit/withdraw/harvest transaction, blocking user funds and reward distribution until the pool ratio moves back above the threshold or the smart converter is disabled by governance.

**Note on confidence:** I was unable to execute this against the live pool math to numerically confirm the fee always exceeds the price premium at every ratio near the threshold; the exact frequency of triggering depends on the wombat pool's swap-fee configuration relative to `buybackThreshold`/`ratio`. The root-cause code pattern (hard-coded `_minRec = _amountIn` instead of a computed expected output for the swapped portion) is confirmed directly in the source, matching the reported bug class of "checking a swap-safety threshold against the wrong reference value."

### Citations

**File:** wombat/WombatStaking.sol (L242-335)
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

    function burnReceiptToken(address _lpToken, uint256 _amount) 
        whenNotPaused _onlyPoolHelper(_lpToken) external {
            IMintableERC20(pools[_lpToken].receiptToken).burn(msg.sender, _amount);
    }


    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L739-753)
```text
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

**File:** wombat/SmartWomConvert.sol (L72-96)
```text
    function estimateTotalConversion(uint256 _amount, uint256 _convertRatio)
        external
        view
        returns (uint256 minimumEstimatedTotal)
    {
        if (_convertRatio > DENOMINATOR)
            revert IncorrectRatio();
            
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
        uint256 amountOut = 0;

        if (buybackAmount > 0) {
            address[] memory tokenPath = new address[](2);
            tokenPath[0] = wom;
            tokenPath[1] = mWom;

            address[] memory poolPath = new address[](1);
            poolPath[0] = womMWomPool;

            (amountOut, ) = IWombatRouter(router).getAmountOut(tokenPath, poolPath, int256(buybackAmount));
        }

        return (amountOut + convertAmount);
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

**File:** wombat/SmartWomConvert.sol (L181-203)
```text
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

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

```

**File:** wombat/SmartWomConvert.sol (L204-205)
```text
        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```
