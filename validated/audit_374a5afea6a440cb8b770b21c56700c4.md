The vulnerability is real. Let me trace the exact code path.

**Trace:**

1. `exactInputSingle` is `payable`. User A calls `multicall([exactInputSingle(...), refundETH()])` but omits `refundETH`, leaving `msg.value` ETH stranded on the router. [1](#0-0) 

2. The `receive()` guard only blocks *direct* ETH transfers from non-WETH addresses — it does **not** prevent ETH accumulation via `msg.value` in payable calls. [2](#0-1) 

3. User B calls `exactInputSingle` with `tokenIn=WETH`. The callback context stores User B as `payer` and WETH as `token`. [3](#0-2) 

4. The pool calls back → `_justPayCallback` → `pay(WETH, userB, pool, value)`. [4](#0-3) 

5. Inside `pay`, the WETH branch reads `address(this).balance` — which includes User A's stranded ETH. If `nativeBalance >= value`, it wraps the contract's ETH and transfers WETH to the pool, **never pulling from User B's allowance**. [5](#0-4) 

---

### Title
Stranded ETH from prior user consumed to fund subsequent WETH swap, causing direct ETH principal loss — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses the router's entire `address(this).balance` to wrap WETH when paying a pool, without any per-user accounting. ETH left on the router by a prior user (e.g., via a `payable` multicall that omitted `refundETH`) is silently consumed to satisfy a subsequent user's WETH payment obligation.

### Finding Description
The WETH hybrid branch in `pay` checks `address(this).balance` and, if non-zero, wraps it first before falling back to `safeTransferFrom`:

```solidity
// PeripheryPayments.sol L73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer's allowance never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance); // only remainder
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

The design assumes `address(this).balance` equals ETH the *current* user intentionally sent for this call. But the router is `payable` (via `multicall` and `exactInputSingle`), and `refundETH` is opt-in. Any ETH left over from a prior call persists across transactions and is treated as freely available for the next WETH payment. [6](#0-5) 

### Impact Explanation
- **User A** loses their ETH permanently — it is wrapped and transferred to the pool to settle User B's swap.
- **User B** has their WETH allowance pull reduced or eliminated entirely, receiving a subsidized or free swap.
- This is direct loss of user principal with no recovery path, meeting High severity thresholds.

### Likelihood Explanation
Any user who sends ETH via a `payable` router call (e.g., `multicall` bundling an ETH-in swap with a forgotten `refundETH`, or a failed/reverted inner call that leaves ETH behind) creates the precondition. The next WETH-paying swap on the same router in the same block exploits it automatically — no attacker coordination required beyond timing.

### Recommendation
Track per-call ETH attribution. The simplest fix: record `msg.value` at the top of each payable entrypoint in transient storage and deduct from that tracked amount inside `pay`, rather than reading the raw `address(this).balance`. Alternatively, require that WETH payments always use `safeTransferFrom` and only allow native ETH wrapping when the caller explicitly sends `msg.value` in the same call (verified via a transient "ETH budget" slot).

### Proof of Concept
```
1. User A: multicall{value: 1 ether}([exactInputSingle(tokenIn=WETH, amountIn=1e18, ...)])
   // omits refundETH — 1 ETH stays on router
2. User B: exactInputSingle{value: 0}(tokenIn=WETH, amountIn=1e18, ...)
   // callback fires → pay(WETH, userB, pool, 1e18)
   // address(this).balance == 1e18 → wraps User A's ETH, transfers WETH to pool
   // User B's WETH allowance: 0 pulled
3. Assert: User A's 1 ETH is gone; User B's swap settled at zero WETH cost to User B.
```

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
