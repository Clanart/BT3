### Title
Hardcoded zero slippage protection on WOM→mWOM buyback swap in ArbWomUp3 - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3._deposit()` calls `SmartWomConvert.convert()` with a hardcoded `_minRec = 0` for the buyback-swap portion of a user's deposit, mirroring the reported bug class of "manual swap without slippage protection." Any ordinary wallet calling the externally-exposed `incentiveDeposit()` with `_mode == 2` triggers an unprotected on-chain swap through the Wombat router, which can be sandwiched by MEV actors to extract value from the user's converted WOM.

### Finding Description
`incentiveDeposit()` is an unprivileged, user-callable entry point [1](#0-0) . When `_mode == 2`, it routes into `_deposit()`, which splits the user's WOM: half is deposited directly into mWOM, and half is passed to `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` — note the third argument, `_minRec`, is hardcoded to `0` [2](#0-1) .

`SmartWomConvert.convert()` forwards to `_convertFor()`, which performs a real on-chain swap of WOM→mWOM through `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` whenever a `buybackAmount` (non-zero portion) is required, and only reverts via `MinRecNotMatch()` if the combined proceeds fall below the caller-supplied `_minRec` [3](#0-2) . Because `ArbWomUp3` always passes `_minRec = 0` for this code path, the `MinRecNotMatch()` check can never trigger, and the swap executes with no floor on the amount received — functionally identical to the `SpotManager`/`ManualSwapLogic` issue described in the report, where a swap is executed through an on-chain pool without any `minAmountOut` enforcement.

This differs from the `SmartWomConvert.smartConvert()` entry point, which computes `_minRec` dynamically from `_amountIn` [4](#0-3) ; `ArbWomUp3` bypasses that protection entirely by calling the lower-level `convert()` with a literal `0`.

### Impact Explanation
Any user who calls `incentiveDeposit(..., _mode = 2)` has a portion of their WOM swapped through the Wombat pool with zero minimum-output protection. A searcher/attacker can sandwich this transaction (front-run to move the pool price unfavorably, let the victim's swap execute at a degraded rate, then back-run to capture the difference), directly extracting value from the user's deposited funds. This is a direct theft of user funds via price manipulation of an unprotected swap, not merely a griefing or gas-only issue.

### Likelihood Explanation
The swap is triggered automatically whenever an ordinary wallet uses `_mode == 2` of `incentiveDeposit()` — no privileged role or special conditions are required. MEV sandwich bots continuously monitor mempools for exactly this kind of unprotected DEX/AMM interaction, making exploitation straightforward and economically motivated whenever meaningful swap sizes occur.

### Recommendation
Add a user-supplied `_minRec` (or equivalent minimum-output) parameter to `incentiveDeposit()`/`_deposit()` and thread it through to `IConverter(smartWomConvert).convert(toSwap, _convertRatio, _minRec, 0)` instead of hardcoding `0`, mirroring the protection already present in `SmartWomConvert.smartConvert()`.

### Proof of Concept
1. Attacker observes a pending `incentiveDeposit(amount, convertRatio, false, 2)` transaction from a victim in the mempool.
2. Attacker front-runs with a large WOM→mWOM (or reverse) swap on the `womMWomPool` Wombat pool used by `SmartWomConvert`, skewing the exchange rate unfavorably for the upcoming victim swap.
3. Victim's transaction executes `_deposit()` → `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` [5](#0-4) , which cannot revert regardless of how bad the received amount is, since `_minRec = 0`.
4. Attacker back-runs to restore the pool price and pockets the difference, at the direct expense of the victim's converted WOM value.

### Citations

**File:** wombat/ArbWomUp3.sol (L88-99)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);
```

**File:** wombat/ArbWomUp3.sol (L189-203)
```text
        } else if (_mode == 2) {
            uint256 toDeposit = _amount / 2;
            uint256 toSwap = _amount - toDeposit;

            // 50% goes to deposit
            IERC20(wom).safeApprove(mWom, toDeposit);
            IMWom(mWom).deposit(toDeposit); 

            // 50% smart smart convert
            IERC20(wom).safeApprove(smartWomConvert, toSwap);
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);

            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);
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

**File:** wombat/SmartWomConvert.sol (L175-207)
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

        obtainedmWomAmount = convertAmount + amountRec;
```
