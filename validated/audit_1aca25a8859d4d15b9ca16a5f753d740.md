### Title
Excess ETH Sent for WETH Exact-Output Swaps Is Not Refunded and Is Permanently Stranded/Stealable — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` wraps only the exact ETH amount the pool requests and leaves any surplus native ETH in the router. Because `refundETH()` is permissionless and sends the entire contract balance to `msg.sender`, any ETH left behind after a swap is immediately stealable by any third party. For `exactOutputSingle` / `exactOutput` callers who must over-send ETH (because the actual input is unknown until the pool executes), this is a guaranteed, unprivileged loss of user principal.

---

### Finding Description

`PeripheryPayments.pay()` handles the WETH branch as follows:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();   // wraps exactly `value`
        IERC20(WETH).safeTransfer(recipient, value);
        // ← nativeBalance - value ETH silently remains in the contract
    } else if (nativeBalance > 0) { ... }
``` [1](#0-0) 

Only `value` (the pool-requested amount) is wrapped and forwarded. Any ETH above that threshold is never touched again by the swap path.

`refundETH()` is the intended recovery mechanism, but it is:
1. **Not called automatically** by any swap function (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).
2. **Permissionless** — it sends the entire contract ETH balance to `msg.sender`, not to the original depositor.

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // ← any caller receives all ETH
    }
}
``` [2](#0-1) 

For `exactOutputSingle`, the user cannot know the actual `amountIn` before the transaction executes; they must supply `msg.value = amountInMaximum` to avoid a revert. The function checks `amountIn > params.amountInMaximum` and returns `amountIn`, but never refunds the difference:

```solidity
if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
_clearExpectedCallbackPool();
// ← no refundETH() call; excess msg.value stays in router
``` [3](#0-2) 

The same gap exists in `exactOutput` (multi-hop): [4](#0-3) 

---

### Impact Explanation

Any user who calls `exactOutputSingle` or `exactOutput` with `tokenIn == WETH` and `msg.value > actualAmountIn` loses the difference. A MEV bot or any observer can call `refundETH()` in the next block and receive the stranded ETH. This is a **direct, unconditional loss of user principal** with no recovery path once the swap transaction is mined without a batched `refundETH()`.

---

### Likelihood Explanation

Exact-output swaps are a standard use case. Users routinely set `amountInMaximum` conservatively above the expected cost to avoid slippage reverts. When `tokenIn` is WETH and the user pays in native ETH, the surplus is structurally guaranteed to be non-zero in any swap where the pool does not consume the full maximum. No special conditions, malicious tokens, or privileged roles are required — any ordinary user calling `exactOutputSingle` with ETH triggers the loss.

---

### Recommendation

Add an automatic ETH refund at the end of every payable swap entry point, or document and enforce that callers must batch `refundETH()` via `multicall`. The simplest fix is to call `refundETH()` (or an internal equivalent that refunds to `msg.sender`) at the tail of `exactOutputSingle` and `exactOutput`:

```solidity
// after _clearExpectedCallbackPool():
uint256 ethLeft = address(this).balance;
if (ethLeft > 0) _transferETH(msg.sender, ethLeft);
```

Alternatively, compute the exact ETH needed before the swap (via a quoter call or by reading the pool state) and revert if `msg.value` exceeds it, mirroring the `amountInMaximum` guard already present for ERC-20 inputs.

---

### Proof of Concept

1. Pool `P` has WETH as `token0` and USDC as `token1`. Current price implies 1 WETH → 2000 USDC.
2. Alice calls `exactOutputSingle` with:
   - `amountOut = 1800 USDC`
   - `amountInMaximum = 1 ETH` (conservative)
   - `msg.value = 1 ETH`
3. Pool executes; actual cost is `0.9 ETH`. `pay()` wraps `0.9 ETH` and sends it to the pool. `0.1 ETH` remains in the router.
4. `exactOutputSingle` checks `0.9 ETH <= 1 ETH` ✓, clears context, and returns — **without refunding the 0.1 ETH**.
5. Bob observes the transaction in the mempool (or the next block), calls `refundETH()`, and receives Alice's `0.1 ETH`.

Alice loses `0.1 ETH` (~$200 at current prices) with no recourse. [5](#0-4) [6](#0-5)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L186-188)
```text
    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
  }
```
