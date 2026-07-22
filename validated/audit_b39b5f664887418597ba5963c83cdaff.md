The `pay()` function in `PeripheryPayments.sol` is the critical code to analyze:

### Title
Unaccounted Router ETH Balance Consumed as WETH Payment for Arbitrary Callers — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the raw `address(this).balance` to decide how much ETH to wrap as WETH when settling a swap callback. Because there is no per-user ETH accounting, any ETH left on the router by a prior caller (e.g., via a `multicall` that omitted `refundETH`) is silently consumed to cover a subsequent user's WETH obligation.

---

### Finding Description

The WETH branch of `pay()` reads the contract's entire native ETH balance and wraps it unconditionally: [1](#0-0) 

```solidity
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
}
```

There is no invariant that `address(this).balance` belongs to the current `payer`. ETH accumulates on the router whenever a `payable` entry point (`multicall`, `exactInputSingle`, etc.) is called with `msg.value` and the caller omits `refundETH`. [2](#0-1) 

The `receive()` guard only blocks *direct* ETH transfers (no calldata); it does not prevent ETH from arriving as `msg.value` on any `payable` function. [3](#0-2) 

When `exactInputSingle` is called, the payer stored in transient context is `msg.sender` (User B), but `pay()` ignores that payer for the ETH-wrapping path entirely: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Direct loss of principal for User A.** User A's ETH is wrapped and transferred to the pool to settle User B's swap. User A receives nothing in return. User B's WETH allowance is never pulled (first branch: `nativeBalance >= value`), so User B effectively swaps for free at User A's expense.

This satisfies the Critical/High threshold: direct loss of user-deposited ETH with no recovery path.

---

### Likelihood Explanation

The scenario is realistic and requires no privileged access:

1. Users routinely call `multicall{value: X}(...)` to pay for ETH→WETH swaps and may omit `refundETH` (e.g., when the swap consumes less than `msg.value`, or when a user sends ETH speculatively).
2. Any subsequent caller with `tokenIn = WETH` triggers the drain automatically — no coordination or special knowledge required.
3. A griever can monitor the mempool for `multicall` calls with `msg.value` and front-run the `refundETH` with their own `exactInputSingle(WETH, ...)`.

---

### Recommendation

Replace the implicit `address(this).balance` consumption with an explicit opt-in: only wrap ETH that the *current* `msg.sender` deliberately sent in the same transaction. One approach is to track `msg.value` at the router entry point and pass it as a parameter to `pay()`, capping the ETH-wrap path to that amount. Alternatively, require callers to pre-wrap ETH to WETH themselves and remove the hybrid ETH/WETH branch entirely.

---

### Proof of Concept

```
1. User A: multicall{value: 1 ether}([exactInputSingle(tokenIn=USDC, ...)])
   // swap succeeds, 1 ETH stays on router (no refundETH in the array)
   // address(router).balance == 1e18

2. User B: exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1e18, ...)
   // _setNextCallbackContext sets payer = User B, token = WETH
   // pool.swap() triggers metricOmmSwapCallback
   // _justPayCallback calls pay(WETH, UserB, pool, 1e18)
   // nativeBalance = 1e18 >= value = 1e18  →  first branch taken
   // WETH.deposit{value: 1e18}()  ← User A's ETH is wrapped
   // WETH.transfer(pool, 1e18)    ← sent to pool for User B's swap
   // User B's WETH.transferFrom is never called

Assert: User A's 1 ETH is gone; User B's WETH balance is unchanged.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
