Audit Report

## Title
Unguarded `refundETH()` allows any caller to drain excess ETH stranded on the router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` is a public, access-control-free function that unconditionally transfers the router's entire ETH balance to `msg.sender`. When a user swaps with native ETH as WETH input and sends `msg.value` greater than `amountIn`, `pay()` deposits only the exact swap amount as WETH, leaving the remainder on the router. Any subsequent caller can drain this stranded ETH by calling `refundETH()` in a separate transaction.

## Finding Description

`refundETH()` contains no ownership or caller check:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

When `token == WETH` in `pay()`, only the exact swap amount `value` is deposited — not the full `address(this).balance`:

```solidity
// PeripheryPayments.sol L73-77
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

The `receive()` guard only blocks plain ETH transfers (no calldata) from non-WETH addresses. It does **not** prevent ETH from being attached to `payable` function calls such as `exactInputSingle{value: X}(...)` or `multicall{value: X}(...)`. Any excess `msg.value` beyond `amountIn` is left on the router with no attribution.

The intended safe pattern — including `refundETH` as the last call in the same `multicall` — is documented in the test suite. If a user omits it (by mistake or by calling `exactInputSingle{value}` directly without a multicall wrapper), the excess ETH is permanently stranded and immediately claimable by anyone.

## Impact Explanation

Direct theft of user ETH. Any ETH stranded on the router from excess `msg.value` in a swap that consumed less than the full amount can be drained by an attacker calling `refundETH()` in the next transaction. There is no minimum threshold — the attacker receives the full stranded balance. This constitutes a Critical-severity direct loss of user principal.

## Likelihood Explanation

The pattern of sending excess ETH and relying on `refundETH` in the same multicall is the documented and tested usage pattern. Users calling `exactInputSingle{value}` directly (without multicall) or forgetting to append `refundETH` will strand ETH. MEV bots monitoring the mempool or block state can trivially detect a non-zero router ETH balance and call `refundETH()` atomically in the next block. The attack requires no special privileges and is repeatable.

## Recommendation

Restrict `refundETH()` so it can only be called within a `multicall` context, or record the original `msg.sender` of the outermost `multicall` in transient storage and require `msg.sender == storedCaller` inside `refundETH`. Alternatively, accept a `recipient` parameter validated against the transient payer context, mirroring how `unwrapWETH9` accepts a `recipient` but is called within the same atomic multicall.

## Proof of Concept

```
1. User calls router.exactInputSingle{value: 1 ether}(
       ExactInputSingleParams({
           tokenIn: WETH, amountIn: 0.5 ether, ...
       })
   );
   // pay() deposits 0.5 ETH as WETH → pool; 0.5 ETH remains on router

2. Attacker (separate tx) calls router.refundETH();
   // balance = 0.5 ETH, _transferETH(attacker, 0.5 ETH)
   // Attacker receives 0.5 ETH; user's excess is gone.
```

The existing test `test_refundETH_sendsBalanceToCaller` directly confirms this behavior — it pre-loads the router with ETH via `vm.deal` and shows any caller receives it:

```solidity
// MetricOmmSimpleRouter.payments.t.sol L74-85
function test_refundETH_sendsBalanceToCaller() public {
    uint256 amount = 2 ether;
    vm.deal(address(router), amount);
    uint256 swapperBefore = swapper.balance;
    vm.prank(swapper);
    router.refundETH();
    assertEq(swapper.balance - swapperBefore, amount, "swapper refunded");
    assertEq(address(router).balance, 0, "router eth cleared");
}
```