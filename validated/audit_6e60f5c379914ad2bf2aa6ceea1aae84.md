## Title
Hardcoded zero-slippage-tolerance in `SmartWomConvert.smartConvert()` can permanently revert `WombatStaking.harvest()`, freezing yield for all stakers - (File: `wombat/SmartWomConvert.sol`)

### Summary
`SmartWomConvert.smartConvert()` is invoked internally by `WombatStaking._sendRewards()` on every `harvest()` call whenever a configured fee is marked `isMWOM`. It hardcodes `_minRec = _amountIn`, i.e. it requires the WOM→mWOM buyback swap plus 1:1 deposit portion to return at least as much mWOM as WOM put in, with zero tolerance for AMM swap fees/slippage. This is the same bug class as the TRST-M-3 report (an unchangeable/too-strict slippage bound causing reverts), except here there is no adjustable parameter at all exposed to relax the requirement, and it sits directly in the fee-distribution path of every harvest.

### Finding Description
In `_convertFor`, the buyback swap through the Wombat router will realistically return `amountRec < buybackAmount` because AMM swaps incur haircut/fees, so `convertAmount + amountRec` will typically be less than `_amountIn` whenever the buyback branch executes: [1](#0-0) 

`smartConvert()` calls `_convertFor` with `_minRec` hardcoded to `_amountIn` (i.e., demanding an exact or better than 1:1 return), rather than accepting a caller- or admin-supplied slippage tolerance: [2](#0-1) 

The buyback branch triggers whenever `currentRatio() < buybackThreshold`, and `buybackThreshold` defaults to `9000` (90%), so the buyback path is the common case, not an edge case: [3](#0-2) 

This function is called unconditionally as part of `WombatStaking._sendRewards()`, which itself is invoked from every `_toMasterWomAndSendReward()` call — i.e., from `deposit()`, `depositLP()`, `withdraw()`, and the permissionless `harvest()` function — whenever a fee with `isMWOM == true` is configured: [4](#0-3) [5](#0-4) 

Unlike the original TRST-M-3 finding — where `MAX_SLIPPAGE` at least allowed some tolerance and the team's fix was to expose an admin-adjustable `maxSlippage` — here the requirement is stricter than any nonzero slippage tolerance (0% tolerance, "at least 1:1"), and there is no setter at all to relax `_minRec` inside `smartConvert()`. `ratio` and `buybackThreshold` only affect how much is routed through the swap vs. the 1:1 deposit path; they cannot make the AMM swap return more than it takes in.

### Impact Explanation
Because `smartConvert()`'s internal swap will structurally fail its own `MinRecNotMatch()` check whenever the buyback branch is exercised (which is the default/common state given the 90% threshold), any `WombatStaking._sendRewards()` call that routes a WOM fee to `smartWomConverter` will revert. Since `_sendRewards` is called from the permissionless `harvest()` entrypoint on every WOM reward distribution to `BaseRewardPool`, this can cause `harvest()` (and deposits/withdrawals that trigger the same internal accounting) to permanently revert for the affected pool, freezing WOM/bonus yield accrual and distribution to `BaseRewardPool` stakers indefinitely — an unprivileged-wallet-reachable freeze of unclaimed yield.

### Likelihood Explanation
High. The buyback branch is the default active condition (`buybackThreshold = 9000`), and any real Wombat AMM swap incurs a small fee/slippage, so `amountRec < buybackAmount` is the expected outcome rather than an edge case. No admin or governance action is required to trigger it — it is inherent to normal `harvest()` operation once `smartWomConverter` and an `isMWOM` fee are configured on `WombatStaking`.

### Recommendation
Expose an admin-adjustable slippage tolerance for `smartConvert()`'s internal buyback swap (analogous to the accepted TRST-M-3 fix), e.g., allow `_minRec` to be computed as `_amountIn * (DENOMINATOR - maxSlippage) / DENOMINATOR` with `maxSlippage` settable by the ADMIN Multisig, or route `smartConvert`'s minRec through `estimateTotalConversion()` with an acceptable tolerance buffer, instead of requiring a strict `>= _amountIn`.

### Proof of Concept
1. Deploy `WombatStaking` with `smartWomConverter` pointed at `SmartWomConvert`, and configure a fee with `isMWOM = true` (default `WombatStaking` flow).
2. `SmartWomConvert.buybackThreshold` is `9000` by default, and under normal market conditions `currentRatio()` (mWom priced in WOM via the Wombat pool) will frequently be below 90%, activating the buyback branch in `smartConvert()`.
3. Any call to `WombatStaking.harvest(_lpToken)` that generates WOM rewards routes the fee portion through `_sendRewards → IConverter(smartWomConverter).smartConvert(feeAmount, 0)`.
4. Inside `smartConvert → _convertFor`, the buyback swap through `IWombatRouter.swapExactTokensForTokens` returns `amountRec` reduced by the pool's swap fee, so `convertAmount + amountRec < _amountIn`, tripping `revert MinRecNotMatch()`.
5. The revert propagates up through `_sendRewards` and `harvest()`, reverting the entire harvest transaction and blocking WOM reward distribution to `BaseRewardPool` for that pool until an admin reconfigures `buybackThreshold`/`smartWomConverter` — freezing unclaimed yield for all stakers of that pool.

### Citations

**File:** wombat/SmartWomConvert.sol (L49-68)
```text
    constructor(
        address _mwom,
        address _wom,
        address _mWomSV,
        address _router,
        address _womMWomPool,
        address _masterMagpie,
        address _womAsset,
        uint256 _ratio
    ) {
        mWom = _mwom;
        wom = _wom;
        router = _router;
        womMWomPool = _womMWomPool;
        masterMagpie = _masterMagpie;
        womAsset = _womAsset;
        ratio = _ratio;
        mWomSV = ILocker(_mWomSV);
        buybackThreshold = 9000;
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

**File:** wombat/SmartWomConvert.sol (L175-206)
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

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

```

**File:** wombat/WombatStaking.sol (L331-335)
```text
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
