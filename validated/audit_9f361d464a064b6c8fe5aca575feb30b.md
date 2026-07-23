Audit Report

## Title
Excess ETH sent to payable swap and liquidity functions is not automatically refunded and can be stolen by any caller — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
The `exactOutputSingle`, `exactOutput`, `addLiquidityExactShares`, and `addLiquidityWeighted` functions are `payable` and accept native ETH for WETH-denominated operations. The internal `pay()` function wraps only the exact required amount of ETH into WETH, leaving any surplus in the contract. Because `refundETH()` is permissionless and sends the full contract ETH balance to `msg.sender`, any third party can call it in a subsequent transaction to steal the excess ETH left by the original user.

## Finding Description
`PeripheryPayments.pay()` handles WETH-leg payments by consuming only `value` wei from the contract's ETH balance:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
```

Any ETH above `value` remains in the contract after the call. The refund helper is unrestricted:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

There is no binding between the original ETH sender and the refund recipient. None of `exactOutputSingle` (L130–147), `exactOutput` (L154–188), `addLiquidityExactShares` (L56–68, L71–81), or `addLiquidityWeighted` (L88–116, L123–149) issue an automatic tail-refund. The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks direct ETH sends with no calldata; it does not prevent ETH from entering via `msg.value` on a payable function call. For exact-output swaps, the caller supplies `amountInMaximum` but the pool charges only the market-determined `amountIn ≤ amountInMaximum`; the gap `amountInMaximum − amountIn` is left stranded with no automatic return path.

## Impact Explanation
Direct loss of user principal. A user who calls `exactOutputSingle{value: 1000}(...)` where the pool charges only 800 wei loses 200 wei to any address that races to call `refundETH()`. The loss is bounded by `amountInMaximum − actual amountIn` and is repeatable for every over-estimated ETH call. For `addLiquidityWeighted`, the probe-then-scale flow makes the final ETH consumption unpredictable at call time, widening the gap further. This meets the Sherlock threshold for direct loss of user principal.

## Likelihood Explanation
Medium. The Uniswap v3-style multicall-plus-`refundETH` pattern is non-obvious to integrators and end users. Any direct call to a payable swap or liquidity function with a conservative (over-estimated) ETH value — a common defensive pattern — silently leaves funds at risk. MEV bots routinely monitor for stranded ETH in known router contracts and can extract it in the very next block.

## Recommendation
Add an automatic ETH refund at the end of each payable entry point:

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable returns (uint256 amountIn)
{
    // ... existing logic ...
    _clearExpectedCallbackPool();
    uint256 leftover = address(this).balance;
    if (leftover > 0) _transferETH(msg.sender, leftover);
}
```

Apply the same tail-refund to `exactOutput`, `addLiquidityExactShares`, and `addLiquidityWeighted`. Alternatively, for exact-input single-hop swaps where `amountIn` is caller-specified, enforce `msg.value == amountIn` when `tokenIn == WETH` to eliminate the surplus entirely.

## Proof of Concept
1. Pool is priced such that swapping to receive 1,000 token1 costs 800 wei of WETH.
2. User calls `router.exactOutputSingle{value: 1000}(params)` where `params.amountInMaximum = 1000` and `params.tokenIn = WETH`.
3. Inside the swap callback, `pay()` wraps 800 wei and transfers it to the pool; 200 wei remains in the router (`PeripheryPayments.sol` L74–77).
4. `exactOutputSingle` returns at L147 without issuing a refund.
5. Attacker calls `router.refundETH()` in the next block; `refundETH()` at L58–63 sends the full 200 wei balance to the attacker.
6. User's net ETH loss: 200 wei above the swap cost, with no recourse.