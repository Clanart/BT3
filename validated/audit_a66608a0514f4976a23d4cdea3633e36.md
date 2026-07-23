Audit Report

## Title
`refundETH()` Has No Caller Attribution — Any Address Can Steal ETH Stranded by a Prior `exactInputSingle{value: excess}` Call — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`exactInputSingle` is `payable` and the `pay()` helper deposits exactly `amountIn` worth of native ETH as WETH, leaving any `msg.value - amountIn` surplus on the router. `refundETH()` is an unrestricted external function with no per-depositor accounting that unconditionally transfers the router's entire ETH balance to `msg.sender`. Any attacker can call it in a subsequent transaction to steal the victim's stranded ETH.

## Finding Description

`exactInputSingle` is declared `payable`, so callers may send arbitrary ETH: [1](#0-0) 

During the swap callback, `pay()` is invoked with `value == amountIn`. When `token == WETH` and `nativeBalance >= value`, it deposits exactly `value` ETH as WETH and forwards it to the pool — the remainder of `address(this).balance` is untouched: [2](#0-1) 

`refundETH()` has no access control and no per-depositor accounting. It reads the full contract balance and transfers it to `msg.sender`: [3](#0-2) 

The `receive()` guard only rejects direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `payable` entry points: [4](#0-3) 

There is no mechanism tying stranded ETH to the original depositor. Any caller of `refundETH()` in a later transaction claims the entire balance.

## Impact Explanation

Direct theft of user ETH principal. A victim who calls `exactInputSingle{value: 1 ether}` with `amountIn = 0.5 ether` loses 0.5 ETH to the first attacker who calls `refundETH()` afterward. The loss equals the victim's `msg.value` overage and is repeatable across every such transaction. This meets the Critical/High threshold for direct loss of user principal.

## Likelihood Explanation

`exactInputSingle` is a standalone `payable` function with no enforcement that it must be called through `multicall`. Any user or integrator that sends excess ETH (e.g., to account for slippage in value estimation) will strand funds. An attacker can monitor the mempool for such calls and back-run them with `refundETH()` at negligible cost. The attack requires no prior setup, no special permissions, and is repeatable.

## Recommendation

1. **Automatic refund at each swap entry point**: after `_clearExpectedCallbackPool()`, unconditionally refund `address(this).balance` to `msg.sender` — matching how Uniswap v3 periphery handles this in later revisions.
2. **Restrict standalone `refundETH()` exposure**: since `multicall` uses `delegatecall`, `msg.sender` is preserved inside a multicall bundle, so a `refundETH` step already sends to the original caller. The cross-transaction theft window is the risk; adding an automatic refund at the end of each swap entry point eliminates the need for users to bundle `refundETH` manually.

## Proof of Concept

```solidity
// Tx 1 — victim
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 0.5 ether,   // pay() deposits exactly 0.5 ETH; 0.5 ETH remains on router
    amountOutMinimum: 0,
    recipient: victim,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 0.5 ether

// Tx 2 — attacker (no prior interaction required)
router.refundETH();
// attacker receives 0.5 ether; victim's ETH is gone
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```
