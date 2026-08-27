### Title
Auto reward-to-mWOM conversion enforces a 1:1 minimum-output requirement that can revert `harvest()`, freezing reward distribution - ([File: wombat/SmartWomConvert.sol])

### Summary
`WombatStaking._sendRewards` automatically routes a configured share of harvested `WOM` fees into `mWOM` via `SmartWomConvert.smartConvert`. This function hardcodes the minimum acceptable output at `_amountIn` (i.e., a 1:1 requirement) regardless of the actual swap price impact through the `womMWomPool`, the same class of "restrictive/misconfigured slippage" bug described in the source report for auto-redemption.

### Finding Description
`WombatStaking._sendRewards` calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` whenever a fee tranche is flagged `isMWOM` and the reward token is `wom` [1](#0-0) .

`smartConvert` forwards to the internal converter with `_minRec` hardcoded to the full input amount (`_amountIn`), not an amount adjusted for expected swap slippage/price impact:
```solidity
function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
    ...
    return _convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode);
}
``` [2](#0-1) 

Inside `_convertFor`, the portion of `WOM` designated for "buyback" is swapped for `mWOM` through the Wombat router with a swap-level `amountOutMin` of `0` (no protection at the swap call itself), and slippage protection is only enforced afterwards by comparing the *total* obtained amount (`convertAmount + amountRec`) against `_minRec`:
```solidity
amountRec = IWombatRouter(router).swapExactTokensForTokens(
    tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
);
...
if (convertAmount + amountRec < _minRec)
    revert MinRecNotMatch();
``` [3](#0-2) 

Because `smartConvert` sets `_minRec = _amountIn`, the overall requirement collapses to `amountRec >= buybackAmount` — i.e., the `WOM → mWOM` leg of the swap must return at least as much value as was put in. This ignores normal AMM price impact/slippage on the `womMWomPool`. `smartConvert` only routes through the buyback path when `currentRatio() < buybackThreshold` (mWOM trading at a discount to WOM) [4](#0-3) , so under normal conditions the swap is expected to yield slightly more than 1:1, but any price impact from swap size, pool imbalance, or a discount narrower than the price impact incurred will push `amountRec` below `buybackAmount`, causing `MinRecNotMatch()` to revert — exactly analogous to the reported "amountOutMinimum doesn't account for slippage" bug class.

### Impact Explanation
A revert in `smartConvert` propagates up through `_sendRewards`, which reverts the entire `_toMasterWomAndSendReward` call, and in turn the entire `harvest()` / `depositLP()` / `withdraw()` transaction on `WombatStaking` (since these all call `_toMasterWomAndSendReward`) [5](#0-4) . `harvest()` is externally callable by any unprivileged wallet and is the mechanism that pushes accrued `WOM`/bonus rewards into the `BaseRewardPool` rewarders via `queueNewRewards` [6](#0-5) , [7](#0-6) . If the hardcoded 1:1 slippage bound cannot be met (e.g., during periods of price impact/low liquidity in the `womMWomPool`), harvesting/reward distribution for that pool is blocked, freezing unclaimed yield destined for reward pools until conditions change or the fee configuration/converter is adjusted by an admin.

### Likelihood Explanation
This triggers under ordinary, unprivileged usage (anyone calling `harvest()`, `depositLP()`, or `withdraw()`) whenever the `WOM→mWOM` buyback-swap price impact on `womMWomPool` exceeds the discount captured by `currentRatio()` — plausible for larger fee amounts or during periods of pool imbalance/thin liquidity, mirroring the report's description that failures are more likely with lower liquidity and when the price is close to the break-even point.

### Recommendation
In `SmartWomConvert.smartConvert`, do not hardcode `_minRec = _amountIn`. Instead, compute an expected output via `estimateTotalConversion`/`getAmountOut` and apply a reasonable buffer/tolerance (as the referenced upstream fix does), or make the minimum-received bound a parameter that accounts for realistic price impact rather than requiring a strict 1:1 (or better) outcome on every internal auto-conversion.

### Proof of Concept
Not independently reproducible from the indexed context (no test harness/pool state available here) — conceptually: reduce `womMWomPool` liquidity or size the harvested `feeAmount` such that swapping `buybackAmount` of `WOM` for `mWOM` yields less than `buybackAmount` in `mWOM` terms even though `currentRatio() < buybackThreshold`; then call `WombatStaking.harvest(lpToken)` and observe the transaction revert with `MinRecNotMatch()` inside `SmartWomConvert._convertFor`, blocking reward distribution for that pool.

### Citations

**File:** wombat/WombatStaking.sol (L271-335)
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

**File:** wombat/WombatStaking.sol (L755-769)
```text
                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
                    }
                }
            }
        }

        IERC20(_rewardToken).safeApprove(_rewarder, 0);
        IERC20(_rewardToken).safeApprove(_rewarder, _amount);
        IBaseRewardPool(_rewarder).queueNewRewards(_amount, _rewardToken);
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

**File:** wombat/SmartWomConvert.sol (L186-207)
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

        obtainedmWomAmount = convertAmount + amountRec;
```
