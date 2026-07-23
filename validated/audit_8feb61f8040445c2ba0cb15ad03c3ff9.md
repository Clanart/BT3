### Title
`refundETH()` Sends Entire Router ETH Balance to Arbitrary `msg.sender`, Enabling Front-Running Theft of User Excess ETH — (`File: metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH()` is an unrestricted `external` function that transfers the router's entire native ETH balance to `msg.sender`. When a user sends more ETH than a WETH-input swap consumes, the unconsumed ETH remains on the router after the swap transaction. Any attacker who monitors the mempool can call `refundETH()` before the user does and steal that ETH.

---

### Finding Description

`PeripheryPayments.refundETH()` contains no caller restriction:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol  L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // ← sends to whoever calls this
    }
}
``` [1](#0-0) 

The `pay()` helper, when `tokenIn == WETH` and the router holds native ETH, deposits exactly `value` wei as WETH and forwards it to the pool, leaving any surplus ETH on the router:

```solidity
// PeripheryPayments.sol  L73-77
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    // surplus (nativeBalance - value) stays on the router
``` [2](#0-1) 

This surplus is not returned to the user inside the swap call. The intended recovery mechanism is for the user to call `refundETH()` — either in a subsequent transaction or bundled in a `multicall`. When called in a separate transaction, the window between the swap transaction and the refund transaction is exploitable.

The `receive()` guard only restricts who can *deposit* ETH via the fallback; it does not protect the balance from being drained by an arbitrary caller through `refundETH()`:

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [3](#0-2) 

---

### Impact Explanation

**Direct loss of user principal (native ETH).** A user who calls `exactInputSingle{value: X}(...)` or `exactOutput{value: X}(...)` with `tokenIn = WETH` and `X > amountIn` (a common pattern for slippage headroom) will have `X − amountIn` ETH stranded on the router after the swap. An attacker who calls `refundETH()` before the user recovers it receives the full stranded balance. The loss is bounded only by how much excess ETH the user sent. [4](#0-3) 

---

### Likelihood Explanation

- **Trigger condition:** User sends ETH with a WETH-input swap and does not bundle `refundETH()` in the same `multicall`. This is a realistic mistake for direct contract callers, scripts, or integrations that call `exactInputSingle` / `exactOutputSingle` directly.
- **Attacker effort:** Zero — monitoring the mempool for `exactInputSingle` calls with `msg.value > amountIn` and submitting a higher-gas `refundETH()` call is trivial.
- **No special privilege required:** `refundETH()` is `external` with no `onlyOwner`, no `msg.sender` check, and no transient-context guard. [1](#0-0) 

---

### Recommendation

Add a `recipient` parameter to `refundETH()` (matching the pattern already used by `unwrapWETH9` and `sweepToken`) so the caller explicitly designates the beneficiary, and document that the function must be called by the original depositor:

```solidity
function refundETH(address recipient) external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(recipient, balance);
    }
}
``` [5](#0-4) 

This alone does not fully prevent theft (an attacker can still pass their own address), so the stronger fix is to track per-caller deposited ETH in transient storage and only allow the depositor to withdraw their own balance. At minimum, all integrations and the router's own documentation must mandate that `refundETH()` is always bundled in the same `multicall` as the swap. [6](#0-5) 

---

### Proof of Concept

```
Setup:
  - WETH address W, pool P (token0=W, token1=USDC), router R
  - User U wants to swap 0.9 ETH → USDC, sends 1 ETH for slippage headroom
  - Attacker A monitors the mempool

Step 1 (User, tx1):
  U calls R.exactInputSingle{value: 1 ETH}({
      pool: P, tokenIn: W, amountIn: 0.9 ETH, ...
  })
  → pay() deposits 0.9 ETH as WETH, forwards to pool
  → 0.1 ETH remains on R
  → tx1 mined; R.balance == 0.1 ETH

Step 2 (Attacker, tx2, higher gas):
  A calls R.refundETH()
  → balance = 0.1 ETH > 0
  → _transferETH(A, 0.1 ETH) succeeds
  → A receives 0.1 ETH; R.balance == 0

Step 3 (User, tx3):
  U calls R.refundETH()
  → balance = 0; nothing transferred
  → U loses 0.1 ETH permanently
``` [1](#0-0) [2](#0-1)

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

**File:** metric-periphery/contracts/interfaces/IPeripheryPayments.sol (L18-19)
```text
  /// @notice Refund all ETH held by this contract to `msg.sender`.
  function refundETH() external payable;
```
