### Title
Unused ETH Is Not Refunded After Partial Fill in `exactInputSingle` When a Price Limit Is Set — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`MetricOmmSimpleRouter.exactInputSingle` is `payable` and the `pay` helper in `PeripheryPayments` supports native-ETH-to-WETH wrapping. When a caller sends ETH, sets `tokenIn = WETH`, and supplies a non-trivial `priceLimitX64`, the pool may partially fill the swap (stopping when the price limit is reached). The swap callback pays only the **actual** amount consumed; the remaining ETH is silently left in the router. Because `refundETH` is a public, permissionless function, any third party (e.g., a MEV bot) can immediately drain the stranded ETH.

---

### Finding Description

**Step 1 – Native ETH path is live.**

`PeripheryPayments.pay` explicitly handles the case where `token == WETH` and `address(this).balance >= value`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        ...
    }
``` [1](#0-0) 

So a caller who sends ETH with `tokenIn = WETH` will have that ETH wrapped and forwarded to the pool during the callback.

**Step 2 – Callback pays only the actual consumed amount.**

`_justPayCallback` extracts the positive delta returned by the pool and pays exactly that:

```solidity
function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
}
``` [2](#0-1) 

If the pool partially fills the swap (price limit hit), `amount0Delta`/`amount1Delta` reflect only the partial input. The callback wraps and forwards only that partial amount. The difference between `msg.value` and the partial amount remains as raw ETH in the router.

**Step 3 – `exactInputSingle` has no post-swap refund.**

After the swap, the function only checks the output minimum and clears transient context. There is no `refundETH()` call:

```solidity
amountOut = MetricOmmSwapInputs.int128ToUint128(out);
if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
_clearExpectedCallbackPool();
``` [3](#0-2) 

Compare this with `exactInput` (multi-hop), which **does** guard against partial fills with `InvalidInputAmountAtHop`:

```solidity
int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);
``` [4](#0-3) 

`exactInputSingle` has no equivalent guard, so a partial fill silently succeeds and leaves ETH stranded.

**Step 4 – `refundETH` is permissionless.**

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [5](#0-4) 

Any address can call this and receive all ETH currently held by the router, including the victim's unspent ETH.

---

### Impact Explanation

A user who calls `exactInputSingle` with `tokenIn = WETH`, sends `N` ETH as `msg.value`, and sets a `priceLimitX64` that is reached mid-swap will:
- Receive fewer output tokens than `N` ETH would buy at the open market.
- Lose the unspent ETH portion to any MEV bot that calls `refundETH()` in the same or next block.

This is a **direct, permanent loss of user principal** with no recovery path once the ETH is drained.

---

### Likelihood Explanation

- Setting a price limit (`priceLimitX64 != 0` for `zeroForOne`, or `!= type(uint128).max` for `!zeroForOne`) is a standard, documented feature of the router interface.
- Sending native ETH instead of pre-wrapped WETH is the natural UX for most users.
- MEV bots routinely monitor for stranded ETH in router contracts and call `refundETH()` atomically.
- No privileged access or malicious setup is required; any ordinary user transaction triggers the loss.

---

### Recommendation

Add an automatic ETH refund at the end of `exactInputSingle` (and `exactOutputSingle`) when native ETH may have been used:

```solidity
function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    // ... existing logic ...
    _clearExpectedCallbackPool();

    // Refund any unspent ETH back to the caller
    if (address(this).balance > 0) {
        _transferETH(msg.sender, address(this).balance);
    }
}
```

Alternatively, document that callers **must** wrap `exactInputSingle` in a `multicall` that appends a `refundETH()` call whenever native ETH is sent, and enforce this at the interface level (e.g., by making the function non-payable and requiring pre-wrapped WETH).

---

### Proof of Concept

```
1. Alice wants to sell 1 ETH for token T, but only up to price P.
2. Alice calls exactInputSingle({
       pool: ETH/T pool,
       tokenIn: WETH,
       zeroForOne: true,
       amountIn: 1e18,
       amountOutMinimum: 0,
       priceLimitX64: P   // non-trivial limit
   }, { value: 1e18 });
3. The pool fills only 0.6 ETH worth before hitting price P.
4. metricOmmSwapCallback fires; pay() wraps and forwards 0.6e18 ETH to the pool.
5. exactInputSingle returns; 0.4e18 ETH remains in the router.
6. Alice receives tokens worth 0.6 ETH but has spent 1 ETH.
7. Bob (MEV bot) calls router.refundETH() and receives 0.4e18 ETH.
8. Alice has permanently lost 0.4 ETH with no recourse.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L82-86)
```text
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L114-115)
```text
      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```
