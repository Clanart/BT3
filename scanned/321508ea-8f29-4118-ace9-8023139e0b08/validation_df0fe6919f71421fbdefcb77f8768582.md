### Title
Excess native ETH sent to `exactOutputSingle` / `exactOutput` is not refunded and is permanently stealable by any caller — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH to fund WETH-input swaps. The `pay()` helper wraps only the exact `amountIn` the pool requests, leaving any excess `msg.value` sitting on the router. Neither function refunds the surplus. Because `refundETH()` sends the entire contract ETH balance to `msg.sender` (not to the original swap caller), any third party can immediately call `refundETH()` in a subsequent transaction and steal the leftover ETH.

---

### Finding Description

`PeripheryPayments.pay()` handles native ETH when `token == WETH`:

```solidity
// PeripheryPayments.sol L73-84
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;
  if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();          // wraps exactly `value`
    IERC20(WETH).safeTransfer(recipient, value);   // excess stays as ETH
  } else if (nativeBalance > 0) { … }
  else { … }
}
``` [1](#0-0) 

When `nativeBalance > value`, only `value` wei is wrapped; the remainder stays as raw ETH on the router. Neither `exactOutputSingle` nor `exactOutput` calls `refundETH()` before returning:

```solidity
// MetricOmmSimpleRouter.sol L130-147
function exactOutputSingle(…) external payable returns (uint256 amountIn) {
    …
    if (amountIn > params.amountInMaximum) revert InputTooHigh(…);
    _clearExpectedCallbackPool();
    // ← no refundETH() here
}
``` [2](#0-1) 

`refundETH()` is permissionless and sends the full contract balance to `msg.sender`:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
  uint256 balance = address(this).balance;
  if (balance > 0) {
    _transferETH(msg.sender, balance);   // any caller receives all ETH
  }
}
``` [3](#0-2) 

The `receive()` fallback blocks unsolicited ETH from non-WETH addresses, but the `payable` swap entry-points bypass it entirely, so the router can accumulate ETH from any caller who sends excess `msg.value`. [4](#0-3) 

---

### Impact Explanation

A user who calls `exactOutputSingle{value: X}(…)` where `X > amountIn` (e.g., they add a slippage buffer in native ETH) loses `X − amountIn` ETH permanently if they do not atomically bundle a `refundETH()` call in the same `multicall`. A front-running bot observing the pending transaction can call `refundETH()` in the very next block and receive the full stranded balance. The loss is bounded only by the user's slippage buffer, which can be arbitrarily large. This is a direct loss of user principal with no recovery path once the transaction is confirmed.

---

### Likelihood Explanation

The pattern is realistic: users interacting with `exactOutputSingle` for WETH-input swaps commonly send a native ETH buffer (e.g., `amountInMaximum` worth of ETH) to avoid a separate WETH approval. The interface NatSpec contains no warning that excess ETH will not be refunded automatically. Any MEV bot monitoring the mempool can steal the surplus in the same block with a trivial `refundETH()` call. [2](#0-1) 

---

### Recommendation

Add an automatic ETH refund at the end of every `payable` swap entry-point when the caller is an EOA (i.e., not inside a `multicall` delegatecall chain). The simplest fix is to append a `refundETH()` call at the tail of `exactOutputSingle` and `exactOutput`:

```solidity
function exactOutputSingle(…) external payable returns (uint256 amountIn) {
    …
    _clearExpectedCallbackPool();
    // Refund any unused native ETH to the caller
    uint256 leftover = address(this).balance;
    if (leftover > 0) _transferETH(msg.sender, leftover);
}
```

Alternatively, document clearly that callers **must** wrap these calls in `multicall([…, refundETH()])` when sending native ETH, and add an on-chain guard that reverts if `address(this).balance > 0` after the swap when not inside a multicall context.

---

### Proof of Concept

```
1. Alice wants to buy exactly 1 000 token1 using native ETH (WETH pool).
2. She estimates amountIn ≈ 1.5 ETH and sends 2 ETH as a buffer:
       router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams{
           tokenIn: WETH, amountOut: 1_000, amountInMaximum: 2 ether, …
       });
3. Pool consumes 1.5 ETH worth of WETH; pay() wraps 1.5 ETH, leaving 0.5 ETH on router.
4. exactOutputSingle returns; 0.5 ETH sits on the router.
5. Bob (front-runner) calls router.refundETH() in the next transaction.
6. refundETH() sends address(this).balance (0.5 ETH) to Bob.
7. Alice loses 0.5 ETH with no recourse.
``` [5](#0-4) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

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
