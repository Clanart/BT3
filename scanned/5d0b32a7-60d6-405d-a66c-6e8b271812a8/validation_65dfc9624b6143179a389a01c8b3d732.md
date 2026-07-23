### Title
Residual ETH Stranded in Router via Payable Swap Calls Is Silently Consumed by Subsequent WETH-Input Swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay` checks `address(this).balance` before pulling WETH from the payer. Any ETH left in the router from a prior payable call (e.g., `exactInputSingle{value: X}` with a non-WETH `tokenIn`) is silently deposited as WETH and forwarded to the pool on behalf of the next WETH-input swap, reducing or eliminating what the second user pays from their own balance and permanently destroying the first user's ETH.

---

### Finding Description

`PeripheryPayments.receive()` rejects plain ETH transfers from non-WETH senders: [1](#0-0) 

However, every swap entry-point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`) is declared `external payable`: [2](#0-1) 

`receive()` is **not** invoked when ETH is sent as `msg.value` to a named `payable` function. The ETH is accepted silently. If the swap's `tokenIn` is not WETH, `pay` takes the `else` branch and calls `safeTransferFrom` — the ETH is never touched and remains in the router: [3](#0-2) 

When the next user calls `exactInputSingle` with `tokenIn = WETH`, the callback reaches `pay(WETH, payer, pool, value)`. The function reads `address(this).balance` first: [4](#0-3) 

If `nativeBalance >= value`, the entire payment is sourced from the router's ETH balance — `payer`'s WETH allowance is never touched. If `0 < nativeBalance < value`, the router's ETH covers the partial amount and only `value - nativeBalance` is pulled from `payer`. In both cases the original ETH depositor's funds are permanently consumed.

---

### Impact Explanation

- **User A** (victim): calls `exactInputSingle{value: 1 ETH}(params)` with a non-WETH `tokenIn`, forgets to call `refundETH()`. Their 1 ETH is silently stranded in the router and later consumed — **direct, permanent loss of principal**.
- **User B** (beneficiary / attacker): calls `exactInputSingle(params)` with `tokenIn = WETH`, `amountIn = 1 ETH`. The router deposits user A's ETH as WETH and forwards it to the pool. User B pays **0 WETH** from their own balance for a 1 ETH swap.

The same scenario applies to `multicall{value: X}(...)` calls that omit a `refundETH()` step, which is a common pattern in router UIs.

---

### Likelihood Explanation

- All swap functions are `payable`, so wallets and front-ends can silently attach ETH to any swap call.
- `multicall` batches are a standard pattern; omitting `refundETH()` is a realistic user error.
- An on-chain attacker can watch the mempool for stranded-ETH transactions and immediately follow with a WETH-input swap to drain the balance.
- No privileged access, no malicious pool, no non-standard token required.

---

### Recommendation

Remove the native-ETH shortcut from `pay` entirely, or gate it strictly to ETH that was intentionally sent in the **same transaction** (e.g., track `msg.value` in transient storage at entry and only allow `pay` to consume up to that amount). The simplest safe fix is to remove the `nativeBalance` branches from `pay` and require callers to explicitly wrap ETH via `multicall([wrap, swap, refundETH])`:

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else {
        IERC20(token).safeTransferFrom(payer, recipient, value);
    }
}
```

Alternatively, cap the consumable native balance to `msg.value` stored in transient storage at the top of each swap entry-point, so only the current caller's ETH can be used.

---

### Proof of Concept

```solidity
// 1. User A accidentally sends ETH with a non-WETH swap
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: address(token1),   // NOT WETH — ETH is ignored by pay()
    tokenOut: address(token2),
    amountIn: 1000,
    ...
}));
// router.balance == 1 ether; user A's ETH is stranded

// 2. User B exploits the stranded ETH
uint256 wethBefore = weth.balanceOf(userB);
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 1 ether,
    ...
}));
// pay() sees nativeBalance (1 ETH) >= value (1 ETH)
// deposits user A's ETH as WETH, transfers to pool
// userB's WETH balance is unchanged: weth.balanceOf(userB) == wethBefore
// user A's 1 ETH is gone
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```
