Confirmed: `mWOM.deposit()` calls `_convert(_amount, false, false)` which mints exactly `_amount` mWOM to the caller via `_mint(msg.sender, _amount)` [1](#0-0) .

`amountRec` is not a theoretical/arithmetic value either — it is the actual return value of `IWombatRouter(router).swapExactTokensForTokens(...)`, which represents the real amount of mWOM the contract received from the swap (the router transfers output tokens to `address(this)` and returns the amount actually transferred) [2](#0-1) . So `obtainedmWomAmount` is effectively the sum of (a) an exact 1:1 mint and (b) the actual swap output reported by the router — not a discrepancy-prone arithmetic estimate divorced from real balances.

If a sandwich attack drives the pool price against the buyback leg, `amountRec` would simply be lower, and the `MinRecNotMatch()` check reverts the whole transaction if `convertAmount + amountRec < _minRec` [3](#0-2) . There is no scenario where the contract credits a user more mWOM than it actually received: the mint portion is always exact, and the swap portion is bounded by `_minRec` (attacker-supplied, and if set too low by an attacker calling `convert` on themselves, they only harm themselves, since `_for` is `msg.sender` in `convert()`) or is capped in the `smartConvert`/staking flow to `_amountIn` as `_minRec` [4](#0-3) . There is no shared-balance mismatch mechanism here that an unprivileged attacker could exploit to steal other users' funds — worst case an attacker degrades their own conversion rate or the transaction reverts.

### Citations

**File:** wombat/mWOM.sol (L86-88)
```text
    function deposit(uint256 _amount) whenNotPaused external {
        _convert(_amount, false, false);
    }
```

**File:** wombat/SmartWomConvert.sol (L121-147)
```text
    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
    }

    function convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        external
        returns (uint256 obtainedmWomAmount)
    {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, _for, _mode);
    }

    // should mainly used by wombat staking upon sending wom
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
