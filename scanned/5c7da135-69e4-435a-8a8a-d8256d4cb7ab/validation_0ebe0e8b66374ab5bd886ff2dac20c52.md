The vulnerability is real. Here is the full analysis:

---

### Title
Leftover ETH in Router Consumed for Subsequent User's WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

The `pay` function's WETH hybrid branch uses `address(this).balance` without any per-user accounting. Any ETH left in the router by a prior user (via a `payable` `multicall` that omitted `refundETH`) is silently consumed to satisfy a subsequent user's WETH swap obligation, causing the prior depositor to lose their ETH.

### Finding Description

`PeripheryPayments.pay` handles WETH payments with a three-branch hybrid: [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` — the **total** native ETH held by the router — and uses it to wrap WETH before pulling any remainder from `payer`. There is no per-user ETH accounting; the function cannot distinguish whose ETH is sitting in the contract.

ETH can legitimately accumulate in the router because `multicall` is `payable`: [2](#0-1) 

The `receive()` guard only blocks plain ETH transfers (not function-call ETH): [3](#0-2) 

So a user who calls `multicall{value: 1 ether}([...])` without including `refundETH` leaves 1 ETH stranded in the router. The next user who calls `exactInputSingle` with `tokenIn=WETH` triggers `_justPayCallback` → `pay(WETH, userB, pool, value)`, which finds `nativeBalance >= value`, wraps the stranded ETH, and sends it to the pool — pulling **zero** from `userB`. [4](#0-3) 

### Impact Explanation

User A loses their ETH permanently — it is wrapped and transferred to the pool on behalf of User B. User B's WETH `transferFrom` is skipped entirely (or reduced), so User B receives a subsidized or free swap. This is a direct loss of user principal with no recovery path.

### Likelihood Explanation

Any user who sends ETH via `multicall` for a native-ETH swap and forgets `refundETH` (a common pattern in Uniswap-style routers) creates the precondition. A griever or opportunistic MEV bot can then immediately call `exactInputSingle` with `tokenIn=WETH` to drain the stranded ETH. The attack requires no privileged access and no malicious pool.

### Recommendation

Track per-user ETH deposits (e.g., via transient storage keyed to `msg.sender`) and only allow `pay` to wrap ETH up to the amount the current caller deposited in the same transaction. Alternatively, restrict the WETH hybrid branch to only wrap ETH when `msg.value > 0` in the current call context, and require that `msg.value` exactly covers the native portion.

### Proof of Concept

1. User A calls `router.multicall{value: 1 ether}([swapCallData])` — the inner swap does not use ETH — and omits `refundETH`. `address(router).balance == 1 ether`.
2. User B calls `router.exactInputSingle({tokenIn: WETH, amountIn: 1e18, ...})` with a valid WETH allowance of 1e18.
3. The pool calls `metricOmmSwapCallback`; `_justPayCallback` calls `pay(WETH, userB, pool, 1e18)`.
4. `nativeBalance = 1 ether >= 1e18` → branch at line 75 executes: wraps 1 ETH, transfers WETH to pool. `safeTransferFrom(userB, ...)` is **never called**.
5. User A's 1 ETH is gone. User B's WETH balance is untouched. [5](#0-4)

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
