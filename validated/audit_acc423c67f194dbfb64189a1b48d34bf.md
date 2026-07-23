Audit Report

## Title
Native ETH Overpayment Permanently Stranded and Stealable via Unguarded `refundETH()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
When a user calls any payable swap function (e.g., `exactOutputSingle`) with WETH as `tokenIn` and sends ETH via `msg.value`, `pay()` wraps only the exact pool-demanded amount, leaving any surplus ETH silently stranded in the router. No automatic refund is issued at swap exit. Because `refundETH()` sends the entire contract ETH balance to `msg.sender` with no access control or payer tracking, any third party can immediately drain the surplus ETH, causing direct loss of user principal.

## Finding Description
**Root cause — `pay()` wraps only the exact pool-demanded amount:**

In `PeripheryPayments.sol`, when `token == WETH` and `nativeBalance >= value`, the branch wraps exactly `value` wei and transfers it to the pool: [1](#0-0) 

The remainder `nativeBalance − value` is never touched and stays in the router.

**No post-swap refund in any swap entry point:**

`exactOutputSingle` ends with a slippage check and `_clearExpectedCallbackPool()` — no ETH refund: [2](#0-1) 

The same is true for `exactInputSingle` (line 85), `exactInput` (line 124), and `exactOutput` (line 187). [3](#0-2) 

**`refundETH()` sends to `msg.sender`, not the original payer:** [4](#0-3) 

There is no access control, no payer tracking, and no per-user accounting. Any address that calls `refundETH()` after a swap that left residual ETH in the router receives the full balance.

**Exploit flow:**
1. Alice calls `exactOutputSingle{value: 1 ether}(params)` where `params.tokenIn = WETH`, `params.amountInMaximum = 1 ether`.
2. The pool executes the swap and calls `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, Alice, pool, 0.8 ether)`.
3. `pay()` enters the `nativeBalance >= value` branch: wraps 0.8 ETH → sends 0.8 WETH to pool. The remaining **0.2 ETH stays in the router**.
4. `exactOutputSingle` returns; no refund is issued.
5. Bob observes the router's ETH balance (0.2 ETH) and calls `refundETH()`.
6. `refundETH()` sends `address(this).balance` (0.2 ETH) to Bob.
7. **Alice loses 0.2 ETH with no recourse.**

## Impact Explanation
Direct loss of user ETH principal. Any user who calls an exact-output swap with WETH as `tokenIn` and sends ETH as `msg.value` (the standard pattern when the exact input cost is unknown at submission time) is at risk. The surplus `amountInMaximum − amountIn` is immediately claimable by any address via `refundETH()`. This meets the Sherlock threshold for Medium/High direct loss of user funds.

## Likelihood Explanation
The scenario is triggered by any user who calls an exact-output swap with WETH as `tokenIn` and sends ETH as `msg.value`. A mempool-watching bot can detect the swap transaction, observe the residual ETH balance, and immediately call `refundETH()` in the next block (or via a bundle) to drain it. No special privileges are required. The `multicall` function exists and allows bundling `refundETH()` calls, but this is not enforced or documented in the function interface, making it easy for users to miss. [5](#0-4) 

## Recommendation
1. **Automatic refund at swap exit.** At the end of each payable swap function, if `address(this).balance > 0`, transfer the remainder back to `msg.sender`:
   ```solidity
   if (address(this).balance > 0) _transferETH(msg.sender, address(this).balance);
   ```
2. **Or restrict `refundETH()` to the original payer.** Store the payer address in transient storage at swap entry and require `msg.sender == storedPayer` inside `refundETH()`.
3. **Or enforce multicall usage.** Revert if ETH remains after a swap without a bundled refund call.

## Proof of Concept
1. Deploy `MetricOmmSimpleRouter` with a WETH-paired pool.
2. Alice calls `exactOutputSingle{value: 1 ether}(params)` where `params.tokenIn = WETH`, `params.amountInMaximum = 1 ether`, `params.amountOut = X` (pool only needs 0.8 ETH).
3. Assert `address(router).balance == 0.2 ether` after the swap.
4. Bob calls `router.refundETH()` from a separate address.
5. Assert Bob's ETH balance increased by 0.2 ETH and Alice received no refund.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L83-86)
```text
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-147)
```text
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
