### Title
Zero-Slippage WOM→mWOM Buyback Swap in `SmartWomConvert` Can Cause Reverting Sandwich Attacks That Freeze Reward Harvesting - (File: wombat/SmartWomConvert.sol)

### Summary
`SmartWomConvert._convertFor` executes the WOM→mWOM buyback leg through `IWombatRouter.swapExactTokensForTokens` with a hardcoded minimum-out of `0`, ignoring per-swap slippage protection entirely (analogous to the 0x adaptor ignoring `trade.limit`). The only protection is a post-hoc aggregate check (`convertAmount + amountRec < _minRec`). This function is invoked from `WombatStaking._sendRewards` via `smartConvert(feeAmount, 0)` as part of the ordinary, unprivileged `harvest()` flow. [1](#0-0) 

### Finding Description
Inside `_convertFor`, the buyback swap is executed with `0` as the minimum output parameter, meaning the AMM swap itself has zero slippage protection and can be executed at an arbitrarily bad price: [2](#0-1) 

The function later checks the *aggregate* result against `_minRec`: [3](#0-2) 

For direct callers of `convert`/`convertFor`, `_minRec` is user-supplied and can be set tightly, offsetting the missing per-swap slippage check. However, `smartConvert` — documented as the function "mainly used by wombat staking upon sending wom" — hardcodes `_minRec = _amountIn`, i.e. it requires the *total* mWOM value returned to be at least 1:1 with the WOM sent in: [4](#0-3) 

This function is reached from `WombatStaking._sendRewards`, which is called unconditionally (no try/catch) whenever `harvest()` is invoked by any wallet and a WOM-denominated fee configured with `isMWOM=true` exists: [5](#0-4) [6](#0-5) 

Because the internal swap ignores slippage (min-out = `0`), an attacker can manipulate the `womMWomPool` price immediately before this internal swap fires (e.g., via a flash-loan sandwich or by simply swapping WOM→mWOM in the same underlying Wombat pool right before any user's `harvest()` transaction), driving the swap's output arbitrarily low. Since the aggregate check requires `convertAmount + amountRec >= _amountIn`, a sufficiently unfavorable swap causes `smartConvert` — and therefore the entire `harvest()` call — to revert, since there is no fallback/graceful handling.

### Impact Explanation
Any unprivileged actor who can move the `womMWomPool` ratio unfavorably (a permissionless, ordinary swap against the underlying Wombat pool) can force `WombatStaking.harvest()` to revert whenever a WOM fee configured with `isMWOM=true` is active, because the `_sendRewards` → `smartConvert` → `_convertFor` call chain has no error handling. This blocks reward harvesting/distribution for that pool for all stakers until the pool ratio recovers past `buybackThreshold`, effectively freezing accrual/distribution of unclaimed yield for the affected pool. Depending on how persistently an attacker is willing to keep the pool skewed (repeated small swaps before each harvest attempt), this can extend well past 24 hours, matching a qualifying "freeze of unclaimed yield" impact.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to hold/borrow WOM and repeatedly front-run `harvest()` calls to keep the pool ratio below `buybackThreshold` at the moment `_sendRewards`/`smartConvert` executes, and it depends on the specific fee configuration (`isMWOM=true` and `smartWomConverter` set) being active on-chain. It does not require any privileged role — any wallet with capital can attempt it, and Wombat pool depth for the WOM/mWOM pool may make repeated skewing economically feasible for a persistent attacker.

### Recommendation
Pass a real, computed minimum output (not `0`) into `IWombatRouter.swapExactTokensForTokens` inside `_convertFor`, derived from an oracle/TWAP or a caller-supplied per-leg slippage parameter, rather than relying solely on the coarse aggregate `_minRec` check. Additionally, consider isolating the `smartConvert` call inside `_sendRewards` with a try/catch (falling back to a plain `mWom.deposit` or forwarding raw WOM) so that a manipulated buyback swap cannot revert the entire `harvest()`/reward-distribution flow for unrelated stakers.

### Proof of Concept
1. Attacker monitors for pending `harvest()` calls on a pool where `isPoolFeeFree=false` and a fee with `isMWOM=true` is configured, with `smartWomConverter` set to `SmartWomConvert`.
2. Attacker swaps a large amount of mWOM→WOM (or WOM→mWOM, depending on desired skew) against the shared `womMWomPool` to push `currentRatio()` below `buybackThreshold`, and/or to reduce `maxSwapAmount()`/pool depth so that the subsequent buyback swap executes at a poor rate.
3. Victim (or automated keeper) calls `WombatStaking.harvest(_lpToken)`, which internally calls `_sendRewards` → `IConverter(smartWomConverter).smartConvert(feeAmount, 0)`.
4. Inside `smartConvert` → `_convertFor`, the buyback swap executes with `minAmountOut = 0` [2](#0-1) , receiving a degraded `amountRec` due to the attacker-manipulated pool state.
5. The aggregate check `convertAmount + amountRec < _minRec` (`_minRec == feeAmount`) fails [3](#0-2) , causing `_convertFor` to revert, which propagates up through `smartConvert`, `_sendRewards`, and `_toMasterWomAndSendReward`, reverting the entire `harvest()` transaction.
6. Attacker repeats this before each harvest attempt, denying reward distribution/harvesting for the pool indefinitely (potentially 24+ hours) until the pool ratio is allowed to normalize.

### Citations

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

**File:** wombat/SmartWomConvert.sol (L204-205)
```text
        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
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

**File:** wombat/WombatStaking.sol (L739-750)
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
```
