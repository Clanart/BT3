The code confirms the claim. Let me verify each step of the exploit path against the actual source.

Audit Report

## Title
Excess ETH sent with `exactInputSingle(tokenIn=WETH)` is not automatically refunded and is stealable by any caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
When a user calls `exactInputSingle` with `tokenIn=WETH` and `msg.value > amountIn`, the `pay()` function wraps only `amountIn` worth of ETH and forwards it to the pool as WETH. The remaining `(msg.value - amountIn)` ETH is left on the router as raw ETH. Because `exactInputSingle` performs no automatic refund, and `refundETH()` sends to `msg.sender` rather than the original swapper, any third party can call `refundETH()` in a subsequent transaction and claim the stranded ETH.

## Finding Description
In `PeripheryPayments.pay()`, when `token == WETH` and `nativeBalance >= value`:

```solidity
// PeripheryPayments.sol L75-77
IWETH9(WETH).deposit{value: value}();        // wraps exactly amountIn
IERC20(WETH).safeTransfer(recipient, value); // sends amountIn WETH to pool
// msg.value - amountIn ETH remains on router — never returned
```

`exactInputSingle` is `payable` and accepts arbitrary `msg.value`, but the function body ends at `_clearExpectedCallbackPool()` with no ETH refund:

```solidity
// MetricOmmSimpleRouter.sol L67-86
function exactInputSingle(...) external payable returns (uint256 amountOut) {
    ...
    _clearExpectedCallbackPool(); // no refundETH() call
}
```

The stranded ETH is then claimable by anyone via:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance); // sends to caller, not original swapper
    }
}
```

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does not protect against this — it only applies to plain ETH transfers, not to ETH entering the contract through a `payable` function call such as `exactInputSingle{value: ...}(...)`.

## Impact Explanation
Any ETH sent above `amountIn` in a direct (non-multicall) `exactInputSingle` call is permanently stranded on the router until a third party calls `refundETH()` and claims it. The original sender has no priority claim. This constitutes a direct loss of user principal equal to `msg.value - amountIn` per affected transaction, meeting the Sherlock threshold for Medium severity.

## Likelihood Explanation
Users who call `exactInputSingle` directly (not via `multicall`) and send `msg.value > amountIn` — e.g., to cover slippage on the ETH side, or by mistake — are affected. The protocol's own test (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth`) shows the correct pattern requires an explicit `refundETH()` call bundled in a multicall, which is non-obvious to integrators calling the function directly. The file-level comment at L8-10 of the test file documents this pattern only in the test suite, not in the production interface.

## Recommendation
Add an automatic ETH refund at the end of `exactInputSingle` (and `exactInput`) when `tokenIn == WETH`:

```solidity
if (params.tokenIn == WETH && address(this).balance > 0) {
    _transferETH(msg.sender, address(this).balance);
}
```

Alternatively, revert if `msg.value > amountIn` when `tokenIn == WETH` to prevent accidental overpayment, and document prominently that callers **must** use `multicall([exactInputSingle(...), refundETH()])` when sending ETH.

## Proof of Concept
```solidity
// Alice calls exactInputSingle directly with excess ETH
router.exactInputSingle{value: amountIn + 1 ether}(
    ExactInputSingleParams({tokenIn: WETH, amountIn: amountIn, ...})
);
// 1 ether is now stranded on the router

// Bob (attacker) calls refundETH() and receives Alice's 1 ether
router.refundETH(); // Bob receives 1 ether
```

Foundry assertion: after `exactInputSingle{value: amountIn + dust}`, assert `address(router).balance == 0` — this assertion fails, confirming the dust is stranded and stealable. The existing test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` at L106-133 of `MetricOmmSimpleRouter.native.t.sol` already demonstrates the correct multicall pattern, implicitly confirming that a direct call without `refundETH()` leaves ETH on the router.