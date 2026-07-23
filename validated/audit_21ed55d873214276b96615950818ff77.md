Audit Report

## Title
Stranded Native ETH on Router Is Silently Consumed by Subsequent WETH Payers, Causing Direct Loss of Prior User's Funds — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` helper in `PeripheryPayments` uses `address(this).balance` (the router's spot native-ETH balance) to satisfy WETH payment obligations with no per-user or per-transaction accounting. Any native ETH left on the router from a prior `payable` call (e.g., `exactInputSingle{value: X}` where only part of `X` is consumed and `refundETH` is omitted) is silently consumed by the next caller who swaps with `tokenIn = WETH`. The prior user's ETH is permanently lost; the subsequent user pays nothing for their WETH leg.

## Finding Description
`PeripheryPayments.pay()` contains the following branch for WETH payments:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol L73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // spot balance, no per-user accounting
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
        // payer's WETH is never pulled
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

**How ETH becomes stranded:** `exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, and `multicall` are all `payable`. A user can send `msg.value = 2 ETH` to `exactInputSingle(tokenIn=WETH, amountIn=1 ETH, ...)`. The `pay()` call inside the swap callback wraps and forwards exactly 1 ETH to the pool, leaving 1 ETH on the router. If the user omits `refundETH`, that 1 ETH is stranded. [2](#0-1) 

The `receive()` guard only blocks plain ETH transfers from non-WETH addresses; it does not prevent ETH from arriving via `msg.value` on any `payable` entry point. [3](#0-2) 

**How the next user steals it:** Any subsequent caller invoking `exactInputSingle(tokenIn=WETH, amountIn=1 ETH)` with zero `msg.value` triggers `_justPayCallback`: [4](#0-3) 

This calls `pay(WETH, payer=userB, pool, 1 ETH)`. Inside `pay`, `nativeBalance = address(this).balance = 1 ETH >= value = 1 ETH`, so the router wraps User A's stranded ETH into WETH and transfers it to the pool — **without ever calling `safeTransferFrom(userB, ...)`**. User B's WETH balance is untouched; User A's ETH is gone.

**Existing guards are insufficient:**
- `receive()` only blocks `address(router).call{value:...}("")` from non-WETH; it does not block `msg.value` on payable functions.
- `refundETH()` sends `address(this).balance` to `msg.sender`, but by the time User A calls it after User B has exploited, the balance is 0. [5](#0-4) 

## Impact Explanation
Direct loss of user principal. User A's native ETH — sent legitimately as `msg.value` for their own swap — is permanently transferred to the pool on behalf of User B. The exact corrupted value is `min(address(this).balance, amountIn)` ETH per exploit transaction. The loss is unbounded per transaction (up to the maximum swap `amountIn`). This meets the Sherlock High/Critical threshold for direct loss of user funds with no privilege required. [6](#0-5) 

## Likelihood Explanation
Medium-to-High. The trigger condition — a user sending `msg.value` to a `payable` swap function with `tokenIn=WETH` and omitting `refundETH` — is a realistic user mistake, especially since `refundETH` is optional and not enforced by the router. The exploit requires no privilege: any address can call `exactInputSingle(tokenIn=WETH)` with zero `msg.value` to drain whatever ETH is currently on the router. MEV bots can monitor the mempool for stranded-ETH transactions and front-run any refund attempt. [7](#0-6) 

## Recommendation
Track the ETH attributable to the current transaction using transient storage: store `msg.value` at entry to each `payable` entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`) and decrement it as it is consumed in `pay()`. In `pay()`, only use native ETH up to the tracked per-transaction budget, not the full `address(this).balance`. Alternatively, follow the Uniswap v3 pattern strictly: add a runtime assertion that `address(this).balance == 0` at the end of every non-payable entry point, and document that callers must always append `refundETH` when sending `msg.value`. [8](#0-7) 

## Proof of Concept
```
Step 1 — User A strands ETH:
  userA.exactInputSingle{value: 2 ETH}(
      tokenIn=WETH, amountIn=1 ETH, ...
  )
  → pay(WETH, userA, pool, 1 ETH) wraps 1 ETH → pool
  → router.balance == 1 ETH (no refundETH called)

Step 2 — User B exploits (no msg.value, no WETH approval needed):
  userB.exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1 ETH, ...)
  → metricOmmSwapCallback fires
  → _justPayCallback → pay(WETH, payer=userB, pool, 1 ETH)
      nativeBalance = 1 ETH  ≥  value = 1 ETH
      WETH.deposit{value: 1 ETH}()   // uses userA's ETH
      WETH.transfer(pool, 1 ETH)     // pool receives WETH
      // safeTransferFrom(userB, ...) is NEVER called
  → userB completes swap for free
  → userA's 1 ETH is permanently lost
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
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
