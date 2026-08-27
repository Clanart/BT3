## Finding [1](#0-0) 

### Title
Unvalidated `_for` recipient address in `SmartWomConvert.convertFor` can permanently misdirect converted mWOM funds - (File: wombat/SmartWomConvert.sol)

### Summary
`SmartWomConvert.convertFor` is a fully permissionless, unprivileged-wallet-callable function that pulls WOM tokens from `msg.sender` and, based on `_mode`, deposits, locks, or directly transfers the resulting mWOM to an arbitrary caller-supplied `_for` address, with no validation that `_for` is the intended/controllable recipient and no recovery path if the wrong address is supplied.

### Finding Description
`convertFor` is declared as an unrestricted `external` function: [2](#0-1) 
It delegates to the internal `_convertFor`, which pulls the caller's WOM via `safeTransferFrom(msg.sender, ...)` and then, depending on `_mode`, either calls `IMasterMagpie(masterMagpie).depositFor(mWom, obtainedmWomAmount, _for)`, calls `mWomSV.lockFor(obtainedmWomAmount, _for)`, or directly performs `IERC20(mWom).safeTransfer(_for, obtainedmWomAmount)`: [3](#0-2) 
At no point is `_for` checked against `msg.sender`, checked for `address(0)`, or otherwise validated before the converted mWOM is credited/locked/transferred to it. This mirrors the reported `BitVMBridge.burn` issue: a permissionless, fund-moving entry point accepts a destination parameter directly from the caller, uses it to route the user's own funds, and provides no on-chain validation or recovery mechanism if the wrong destination is supplied (e.g., a typo'd address, or an address the caller does not actually control such as a contract without withdrawal logic for `mWom`/locked positions).

### Impact Explanation
If a user (or an integrating contract, such as `ManualCompound`, which itself calls conversion flows with a `msg.sender`-controlled destination) supplies an incorrect `_for` value, the converted mWOM is permanently deposited/locked/transferred to an address the depositor does not control, with no mechanism in `SmartWomConvert`, `MasterMagpie`, or `mWomSV` to reverse or reclaim it. This constitutes a permanent freezing/loss of the user's own converted funds — the same class of loss described in the report (loss of principal due to an unvalidated destination parameter with no on-chain safeguard or recovery path).

### Likelihood Explanation
Likelihood is driven purely by ordinary user error (e.g., copy-paste/typo of `_for`, or supplying an unintended contract address) when interacting directly with `convertFor`, since no confirmation, whitelist, or `_for == msg.sender` restriction exists for this externally exposed function.

### Recommendation
Add validation that `_for` is non-zero and, where feasible, restrict `_for` to `msg.sender` (exposing a separate explicitly-labeled "convert for another address" path if third-party routing is a required feature), or emit/require explicit acknowledgment for non-self destinations. Consider mirroring the fix applied in the referenced `bitvm-bridge-contracts` PR #10 by adding an address-validity check before funds are moved.

### Proof of Concept
1. Caller approves and holds WOM tokens.
2. Caller invokes `SmartWomConvert.convertFor(_amountIn, _convertRatio, _minRec, _for, _mode)` with `_for` set to an incorrect address (typo, or a contract that cannot use/withdraw mWOM/locked mWomSV positions). [1](#0-0) 
3. `_convertFor` executes the swap/deposit path and unconditionally sends the resulting mWOM to `_for`: [3](#0-2) 
4. The converted funds are now held/locked at `_for`; the original caller has no function in `SmartWomConvert`, `MasterMagpie`, or `mWomSV` to recover them.

### Citations

**File:** wombat/SmartWomConvert.sol (L121-130)
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
```

**File:** wombat/SmartWomConvert.sol (L209-219)
```text
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
```
