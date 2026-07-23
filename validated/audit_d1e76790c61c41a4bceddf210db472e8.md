Audit Report

## Title
Excess ETH From Exact-Output Swaps Is Not Refunded and Is Stealable by Any Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` wraps only the exact ETH amount the pool requests (`value`), leaving any surplus `msg.value` stranded in the router. `refundETH()` is permissionless and sends the entire contract ETH balance to `msg.sender`, not the original depositor. Because `exactOutputSingle` and `exactOutput` never call `refundETH()` after the swap, any excess ETH sent by a user is immediately stealable by any third party.

## Finding Description
When a user calls `exactOutputSingle` or `exactOutput` with `tokenIn == WETH` and native ETH (`msg.value = amountInMaximum`), the ETH lands in the router's balance. During the swap callback, `_justPayCallback` calls `pay()`: [1](#0-0) 

When `nativeBalance >= value`, only `value` (the pool-requested amount) is wrapped and forwarded. The remainder `nativeBalance - value` is silently left in the contract.

After the swap, `exactOutputSingle` checks the slippage guard and clears context — but never refunds: [2](#0-1) 

The same gap exists in `exactOutput`: [3](#0-2) 

`refundETH()` has no access control — it sends the full contract ETH balance to whoever calls it: [4](#0-3) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only applies to plain ETH transfers, not to `msg.value` attached to payable function calls, so it provides no protection here. [5](#0-4) 

## Impact Explanation
Direct, unconditional loss of user principal. Any user calling `exactOutputSingle` or `exactOutput` with native ETH and `amountInMaximum > actualAmountIn` loses the difference. A MEV bot or any observer can call `refundETH()` in the same or next block and receive the stranded ETH. No privileged role, special token, or malicious setup is required. This meets the Critical/High threshold for direct loss of user principal.

## Likelihood Explanation
Exact-output swaps are a standard use case. Users must set `amountInMaximum` conservatively above the expected cost to avoid `InputTooHigh` reverts due to price movement. When `tokenIn` is WETH and the user pays in native ETH, a non-zero surplus is structurally guaranteed in any swap where the pool does not consume the full maximum. No special conditions are required — any ordinary user calling `exactOutputSingle` with ETH triggers the loss.

## Recommendation
Add an automatic ETH refund at the tail of every payable exact-output entry point, before returning:

```solidity
// In exactOutputSingle and exactOutput, after _clearExpectedCallbackPool():
uint256 ethLeft = address(this).balance;
if (ethLeft > 0) _transferETH(msg.sender, ethLeft);
```

Alternatively, enforce that callers must batch `refundETH()` via `multicall` and document this as a hard requirement, though the automatic refund is safer and eliminates the MEV window entirely.

## Proof of Concept
1. Pool `P` has WETH as `token0` and USDC as `token1`. Current price implies 1 WETH → 2000 USDC.
2. Alice calls `exactOutputSingle` with `amountOut = 1800 USDC`, `amountInMaximum = 1 ETH`, `msg.value = 1 ETH`.
3. Pool executes; actual cost is `0.9 ETH`. `pay()` wraps `0.9 ETH` and sends it to the pool. `0.1 ETH` remains in the router.
4. `exactOutputSingle` checks `0.9 ETH <= 1 ETH` ✓, calls `_clearExpectedCallbackPool()`, and returns — without refunding the `0.1 ETH`.
5. Bob calls `refundETH()` and receives Alice's `0.1 ETH`.

Alice loses `0.1 ETH` with no recourse.

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-147)
```text
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
