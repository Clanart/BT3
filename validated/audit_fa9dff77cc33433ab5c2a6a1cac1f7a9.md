### Title
Untracked Native ETH Balance in `PeripheryPayments.pay()` Allows Any Caller to Drain Stranded ETH — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` function uses the router's entire `address(this).balance` to subsidize WETH swap payments. ETH left on the router after a partial swap (e.g., when a price limit is hit before consuming all of `amountIn`) is not tracked per-depositor. Any subsequent caller who swaps WETH can consume that stranded ETH for free, causing direct loss of the original depositor's principal.

---

### Finding Description

In `PeripheryPayments.pay()`, when `token == WETH` and `payer != address(this)`, the function reads the contract's **total** native ETH balance and uses it to wrap-and-transfer before pulling any remainder from the payer's WETH allowance: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire contract balance
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

The contract has no per-user ETH accounting. ETH arrives via `msg.value` on any `payable` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`). If a swap is partial — the pool hits a price limit before consuming all of `amountIn` — the callback is invoked with only the consumed amount, wrapping that portion and leaving the surplus on the contract. [2](#0-1) 

The user must call `refundETH()` to recover the surplus. If they do not (e.g., they call `exactInputSingle` directly rather than via `multicall`, or they omit `refundETH()` from the batch), the surplus ETH sits on the contract with no owner record. The next caller who swaps WETH with `msg.value = 0` will have their payment fully or partially covered by the stranded ETH, consuming it without authorization. [3](#0-2) 

---

### Impact Explanation

Direct loss of user ETH principal. The stranded ETH is silently transferred to a pool on behalf of an unrelated caller. The loss equals the ETH surplus left after a partial WETH swap. There is no cap on the amount; a user who sends a large `msg.value` with a tight price limit can lose the entire surplus in a single subsequent block.

---

### Likelihood Explanation

Medium. The precondition is that ETH is left on the router — which occurs whenever:

1. A user calls a `payable` swap function directly (not via `multicall`) and the pool hits the price limit before consuming all of `amountIn`, **or**
2. A user's `multicall` omits `refundETH()` after a WETH swap.

Once ETH is stranded, exploitation is trivial and permissionless: any caller can observe `address(router).balance > 0` and call `exactInputSingle` with `tokenIn = WETH`, `amountIn ≤ balance`, and `msg.value = 0`. No special role or setup is required.

---

### Recommendation

Track each depositor's ETH contribution in transient storage at the start of each `payable` entry point and restrict `pay()` to consume only that recorded amount. Alternatively, after each swap callback, assert that `address(this).balance` has not decreased beyond what the current call's `msg.value` permits, and revert if excess ETH would be consumed from a prior depositor.

---

### Proof of Concept

```
1. User A calls exactInputSingle({
       tokenIn: WETH,
       amountIn: 1e18,
       priceLimitX64: <tight limit>,
       recipient: userA,
       ...
   }) with msg.value = 1e18.

2. Pool hits the price limit after consuming 0.5e18.
   Callback fires with value = 0.5e18.
   pay() wraps 0.5e18 ETH → sends to pool.
   Remaining 0.5e18 ETH stays on the router.

3. User A's tx completes (no refundETH() call).
   router.balance == 0.5e18.

4. User B (attacker) calls exactInputSingle({
       tokenIn: WETH,
       amountIn: 0.5e18,
       recipient: userB,
       ...
   }) with msg.value = 0.

5. pay() reads nativeBalance = 0.5e18 >= value = 0.5e18.
   Wraps User A's 0.5e18 ETH → sends to pool on behalf of User B.

Result: User B receives 0.5e18 WETH worth of output for free.
        User A permanently loses 0.5e18 ETH.
``` [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
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
