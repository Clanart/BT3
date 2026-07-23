Audit Report

## Title
Unguarded `refundETH()` allows any caller to steal excess ETH stranded on the router by a prior user's incomplete multicall — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` is a public, access-control-free function that unconditionally transfers the router's entire ETH balance to `msg.sender`. When a user calls `exactInputSingle{value: X}` with `amountIn < X`, `pay()` wraps only the exact swap amount as WETH, leaving the remainder on the router with no attribution. Any subsequent caller in a separate transaction can drain this stranded ETH by calling `refundETH()` directly.

## Finding Description

`refundETH()` contains no ownership check and sends `address(this).balance` to `msg.sender` unconditionally: [1](#0-0) 

When `token == WETH` and `nativeBalance >= value`, `pay()` deposits only the exact swap amount, not the full native balance: [2](#0-1) 

The `receive()` guard only blocks plain ETH transfers (no calldata) from non-WETH addresses: [3](#0-2) 

It does **not** prevent ETH from being attached to payable function calls such as `exactInputSingle{value: X}(...)`. When a user sends `msg.value > amountIn`, the excess ETH is deposited on the router with no per-user attribution. The intended safe pattern requires including `refundETH` as the last call in the same atomic `multicall`: [4](#0-3) 

But if a user calls `exactInputSingle{value}` directly (without a multicall wrapper) or omits `refundETH` from their multicall, the excess ETH is permanently stranded on the router and immediately claimable by any address in a subsequent transaction.

## Impact Explanation

Direct theft of user ETH principal. Any ETH stranded on the router from excess `msg.value` in a swap that consumed less than the full amount can be drained by an attacker calling `refundETH()` in the next transaction. The attacker receives the full stranded balance with no minimum threshold. This is a Critical-severity direct fund loss matching the allowed impact gate of "direct loss of user principal."

## Likelihood Explanation

The pattern of sending excess ETH and relying on `refundETH` in the same multicall is the documented and tested usage pattern. Users calling `exactInputSingle{value}` directly without a multicall wrapper, or forgetting to append `refundETH`, will strand ETH. MEV bots monitoring the mempool or block state can trivially detect a non-zero router ETH balance and call `refundETH()` atomically in the next block. The test `test_refundETH_sendsBalanceToCaller` directly confirms that any caller receives the full router ETH balance: [5](#0-4) 

## Recommendation

Restrict `refundETH()` so it can only be called within a `multicall` context (i.e., via `delegatecall` from `multicall`), or record the original `msg.sender` of the outermost `multicall` in transient storage and require `msg.sender == storedCaller` inside `refundETH`. Alternatively, accept a `recipient` parameter and restrict it to a caller-supplied address validated against the transient payer context, mirroring how `unwrapWETH9` accepts a `recipient` but is called within the same atomic multicall. [6](#0-5) 

## Proof of Concept

```
1. User calls router.exactInputSingle{value: 1 ether}(
       ExactInputSingleParams({
           tokenIn: WETH, amountIn: 0.5 ether, ...
       })
   );
   // pay() branch: nativeBalance(1 ETH) >= value(0.5 ETH)
   // → deposits exactly 0.5 ETH as WETH → pool
   // → 0.5 ETH remains on router, unattributed

2. Attacker (separate tx) calls router.refundETH();
   // balance = 0.5 ETH → _transferETH(attacker, 0.5 ETH)
   // Attacker receives 0.5 ETH; user's excess is gone.
```

The existing test `test_refundETH_sendsBalanceToCaller` directly confirms this behavior — it pre-loads the router with ETH via `vm.deal` and shows any caller receives it. [1](#0-0) [7](#0-6)

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L74-85)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```
