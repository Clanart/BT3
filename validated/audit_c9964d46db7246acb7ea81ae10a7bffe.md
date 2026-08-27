[1](#0-0) 

**No vulnerability found for this question.**

The premise of the question — that mode 1 and mode 2 approve/forward `obtainedmWomAmount` out of the contract's *shared token balance* (`balanceOf(address(this))`) — does not match the actual code. In `_convertFor`, `obtainedmWomAmount` is computed purely arithmetically from the current call's own amounts: `convertAmount` (the WOM amount deposited into `mWom` in this call) plus `amountRec` (the actual return value of `swapExactTokensForTokens` for this call), i.e. `obtainedmWomAmount = convertAmount + amountRec` [2](#0-1) . This value is never read from `IERC20(mWom).balanceOf(address(this))` — it's deterministically tied to the caller's own `_amount`, `_convertRatio` inputs and the swap's actual output for that transaction, so no other caller's stranded/rounded balance can be forwarded to it via mode 1 (`depositFor` to `masterMagpie`) or mode 2 (`lockFor` on `mWomSV`) [3](#0-2) .

The "shared balance forwarded to next caller" pattern described in the question actually exists in a *different* contract, `ArbWomUp3.sol`, where mode 2 explicitly reads `uint256 mWomBal = IERC20(mWom).balanceOf(address(this))` before locking it for the account [4](#0-3) . That is a distinct function/file from the one named in this question (`SmartWomConvert.convert`), and is out of scope for this specific question.

Since the `_minRec` check (`convertAmount + amountRec < _minRec` reverts) is validated against the exact same locally-computed `obtainedmWomAmount` that gets forwarded — there is no divergence between what is checked and what is transferred, and no cross-caller balance leakage in this function.

### Citations

**File:** wombat/SmartWomConvert.sol (L175-220)
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

        if (_mode == 1) {
            IERC20(mWom).safeApprove(masterMagpie, obtainedmWomAmount);
            IMasterMagpie(masterMagpie).depositFor(mWom, obtainedmWomAmount, _for);
        } else if (_mode == 2) {
            IERC20(mWom).safeApprove(address(mWomSV), obtainedmWomAmount);
            mWomSV.lockFor(obtainedmWomAmount, _for);
        } else {
            IERC20(mWom).safeTransfer(_for, obtainedmWomAmount);
        }

        emit mWomConverted(_for, _amount, obtainedmWomAmount, _mode);
    }
```

**File:** wombat/ArbWomUp3.sol (L201-203)
```text
            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);
```
