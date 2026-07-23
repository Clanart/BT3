Audit Report

## Title
Permissionless `refundETH()` allows any caller to steal excess ETH left on the router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` has no access control and unconditionally transfers the router's entire native ETH balance to `msg.sender`. The `pay()` helper deposits only the exact swap-required amount of ETH as WETH, leaving any excess `msg.value` on the router. Any caller in a subsequent transaction can invoke `refundETH()` to claim that stranded ETH, resulting in direct loss of user principal.

## Finding Description

`refundETH()` is implemented with no caller restriction: [1](#0-0) 

When `tokenIn == WETH` and the router holds sufficient native ETH, `pay()` wraps exactly `value` wei and leaves the remainder untouched: [2](#0-1) 

The `receive()` guard only blocks unsolicited ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` on payable swap entry points: [3](#0-2) 

The `multicall()` dispatcher records no transient `msg.sender` context — it is a plain delegatecall loop: [4](#0-3) 

The safe pattern (including `refundETH` as the last call in the same `multicall`) is demonstrated in tests: [5](#0-4) 

However, `refundETH` is a standalone `external` function callable by anyone at any time. There is no mechanism that restricts it to the original depositor or to the same transaction. A user who calls `exactInputSingle{value: 1 ether}(amountIn=0.5 ETH, tokenIn=WETH)` directly (outside a multicall), or who omits `refundETH` from their multicall, leaves 0.5 ETH on the router claimable by any third party.

## Impact Explanation

Direct, unconditional loss of user principal (native ETH). Any ETH stranded on the router between transactions is immediately claimable by an arbitrary EOA or contract via a single `refundETH()` call. No oracle manipulation, pool state dependency, or privileged role is required. This meets the Critical/High threshold for direct loss of user funds.

## Likelihood Explanation

Users sending native ETH for WETH swaps must manually compose a multicall that appends `refundETH` as the last call. Calling `exactInputSingle` directly with `msg.value > amountIn`, or building an incomplete multicall, leaves ETH on the router. MEV bots routinely monitor mempool and router balances for unprotected ETH and can backrun the victim's transaction in the same block. The condition is easy to trigger accidentally and easy to exploit programmatically.

## Recommendation

Restrict `refundETH` to the active multicall context by recording `msg.sender` in transient storage at `multicall` entry and asserting it inside `refundETH`. Alternatively, accept an explicit `recipient` parameter that the caller must supply (matching Uniswap v4's approach), or auto-refund excess ETH at the end of each payable swap entry point rather than relying on a separate call.

## Proof of Concept

```
1. User calls directly (no multicall):
   router.exactInputSingle{value: 1 ether}(
       ExactInputSingleParams({
           tokenIn: WETH, amountIn: 0.5 ETH, ...
       })
   );
   // pay() deposits exactly 0.5 ETH as WETH; 0.5 ETH remains on router.

2. Attacker (next tx, same block via backrun):
   router.refundETH();
   // Sends address(this).balance (0.5 ETH) to attacker — no check.

3. Assert: attacker.balance += 0.5 ETH; user lost 0.5 ETH of principal.
```

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```
