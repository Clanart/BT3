### Title
Sandwichable fee-to-mWOM buyback swap in `SmartWomConvert.smartConvert` allows attacker to steal arbitrage value destined for stakers - ([File: wombat/SmartWomConvert.sol])

### Summary
Every unprivileged call to a pool helper's `harvest()` routes WOM protocol fees through `WombatStaking._sendRewards`, which calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` whenever an active fee entry has `isMWOM = true`. Inside `smartConvert`/`_convertFor`, the actual on-chain swap `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` is executed with `amountOutMin = 0`, i.e. zero slippage protection at the swap level, so an attacker can sandwich this swap to capture the arbitrage margin that should accrue to the protocol/stakers.

### Finding Description
`_sendRewards` ( [1](#0-0) ) is triggered from `_toMasterWomAndSendReward`, which is reachable by any address calling the pool helper's public `harvest()`/`depositLP()` flow ( [2](#0-1) ). No access control gates this trigger — any unprivileged EOA can call `harvest()` on any active pool to force a fee conversion at a time of their choosing.

When the `isMWOM` fee is active, `_sendRewards` approves and calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` ( [3](#0-2) ). `smartConvert` computes a `convertRatio` based on `currentRatio()` (spot price read from the same pool that is about to be swapped in) and calls `_convertFor` with `_minRec = _amountIn` [4](#0-3) .

Inside `_convertFor`, the buyback portion is swapped via `IWombatRouter.swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` — note the hardcoded `0` for `amountOutMin` [5](#0-4) . The only downstream check is the aggregate floor `convertAmount + amountRec < _minRec` where `_minRec` equals the original WOM `_amountIn`, not a fair-market-price-derived minimum [6](#0-5) . This floor is only designed to prevent gross value destruction (ensuring the protocol never nets less mWOM than the WOM it put in); it does not protect the *marginal* arbitrage profit that the buyback swap is intended to capture from a discounted `womMWomPool`.

Exploit flow:
1. Attacker observes `currentRatio()` is below `buybackThreshold` (mWOM trading at a discount), meaning a buyback via `smartConvert` will occur on the next harvest.
2. Attacker front-runs by swapping in `womMWomPool` to push the mWOM price toward (or above) par, shrinking the discount/arbitrage margin available.
3. Attacker (or anyone) calls `harvest()`, triggering `_sendRewards` → `smartConvert` → `_convertFor`, whose internal swap executes at the now-worse (attacker-manipulated) price with `amountOutMin = 0`.
4. Attacker back-runs, reversing their initial swap and pocketing the spread that would otherwise have caused the protocol to receive more mWOM per WOM (i.e., a larger effective buyback discount).
5. As long as `convertAmount + amountRec >= _amountIn` still holds (which is a very loose floor — it only requires "no worse than 1:1"), the transaction does not revert, and the protocol receives less mWOM than a fair, unmanipulated buyback price would have produced, permanently reducing what is queued to `feeInfo.to` (the `BaseRewardPool`) for stakers.

Existing checks that fail to stop this: there is no `nonReentrant` issue here (this is not reentrancy), no oracle/TWAP check, and the only guard (`_minRec = _amountIn`) is a loose sanity floor unrelated to fair spot pricing at buyback time — it does not enforce a slippage tolerance tied to `currentRatio()` computed pre-manipulation, and the inner router call passes `0` for `amountOutMin`.

### Impact Explanation
This is a theft of unclaimed protocol/staker yield: WOM harvested as fees is meant to be converted into mWOM and queued into `BaseRewardPool` via `queueNewRewards` for stakers [7](#0-6) . By sandwiching the zero-slippage internal swap, an attacker extracts value from this conversion, permanently reducing the mWOM amount that ends up backing staker rewards. This matches "theft of unclaimed yield" / reduction of protocol backing reserved for `BaseRewardPool`.

### Likelihood Explanation
- Fully permissionless: `harvest()`/`depositLP()` triggering `_sendRewards` requires no privileged role.
- Attacker needs only flash-loan/self-funded capital to move `womMWomPool` price and reverse it in the same block (classic sandwich), which is generally feasible given AMM pools have finite liquidity.
- Repeatable on every harvest call where the `isMWOM` fee is active and `smartWomConverter` is set (this is a normal operating configuration, not a misconfiguration).
- The attack does not depend on any admin/governance action — it only depends on normal, expected pool activity (someone calling `harvest()`), which happens routinely.

### Recommendation
Add real slippage protection to the buyback swap in `SmartWomConvert._convertFor`: compute an `amountOutMin` for `swapExactTokensForTokens` based on the pre-transaction `currentRatio()`/oracle-derived fair price (with a reasonable tolerance), rather than passing `0`. Additionally, consider using a TWAP-based or governance-configurable minimum-received check independent of `_amountIn`, and/or restrict/incentivize buyback timing to reduce sandwichability (e.g., private mempool relays, commit-reveal, or a dedicated permissioned keeper with off-chain slippage checks).

### Proof of Concept
Foundry fork test plan:
1. Fork BSC at a block where `womMWomPool` has mWOM trading below `buybackThreshold`.
2. Baseline run: call `harvest()` on an active pool with `isMWOM` fee active; record `feeTosend` (mWOM queued to `feeInfo.to`) via `queueNewRewards` event/state, and record `IMWom(mWom).balanceOf` delta in `WombatStaking` around the `smartConvert` call.
3. Sandwich run: fork at same block; have an attacker contract front-run with a large `womMWomPool` swap (WOM→mWOM or mWOM→WOM to move price toward/above `buybackThreshold`) sized to be affordable via flash loan, then call `harvest()`, then back-run reversing the initial swap.
4. Assert that `feeTosend`/mWOM queued to `feeInfo.to` in the sandwich run is strictly less than in the baseline run, while the attacker's own mWOM/WOM balance nets positive after the front-run+back-run pair (proving profit extraction at the protocol's expense).
5. Additionally assert the transaction does not revert (i.e., `convertAmount + amountRec >= _amountIn` still holds), demonstrating the loose `_minRec` check is insufficient to block the attack.

### Citations

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

**File:** wombat/WombatStaking.sol (L755-762)
```text
                    if (!feeInfo.isAddress) {
                        IERC20(rewardToken).safeApprove(feeInfo.to, 0);
                        IERC20(rewardToken).safeApprove(feeInfo.to, feeTosend);
                        IBaseRewardPool(feeInfo.to).queueNewRewards(feeTosend, rewardToken);
                    } else {
                        IERC20(rewardToken).safeTransfer(feeInfo.to, feeTosend);
                        emit RewardPaidTo(feeInfo.to, rewardToken, feeTosend);
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

**File:** wombat/SmartWomConvert.sol (L186-197)
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
```

**File:** wombat/SmartWomConvert.sol (L204-207)
```text
        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

        obtainedmWomAmount = convertAmount + amountRec;
```
