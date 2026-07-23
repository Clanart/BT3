Audit Report

## Title
`PeripheryPayments.pay()` consumes stranded router ETH as WETH settlement, enabling free swaps at victim's expense — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` settles WETH obligations by reading `address(this).balance` — the router's **total** native ETH balance — rather than only the ETH the current caller contributed. Any native ETH left on the router by a prior user (e.g., excess `msg.value` from a `multicall` that omitted `refundETH`) is silently consumed to cover a subsequent caller's WETH payment. The attacker receives the full swap output at zero cost while the victim's ETH is permanently lost.

## Finding Description

In `PeripheryPayments.pay()`, when `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` — the entire router balance — to determine how much native ETH is available for wrapping: [1](#0-0) 

This balance includes ETH stranded from prior callers. ETH accumulates on the router whenever a user sends excess `msg.value` through the payable `multicall` entry point without appending a `refundETH` call: [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` on payable entry points: [3](#0-2) 

For `exactInputSingle`, the callback context stores `payer = msg.sender` and `tokenToPay = params.tokenIn`: [4](#0-3) 

When the pool calls back, `_justPayCallback` invokes `pay()` with the attacker as `payer`: [5](#0-4) 

In the full-coverage case (`nativeBalance >= value`), `pay()` wraps exactly `value` ETH from the router's balance and transfers it to the pool — **no `safeTransferFrom` is issued against the attacker**. The attacker pays nothing.

The test suite explicitly documents and tests this multicall-with-ETH pattern, confirming that `refundETH` must be explicitly included to recover unused ETH: [6](#0-5) [7](#0-6) 

## Impact Explanation

Direct loss of user principal. Any user who sends excess ETH in a `multicall` without including `refundETH` has their ETH permanently claimable by any subsequent caller who issues a WETH-input swap. In the full-coverage case the attacker pays nothing for the swap output; in the partial-coverage case the attacker's WETH cost is reduced by the stranded amount. The victim receives no compensation and cannot recover the ETH (it is wrapped and transferred to the pool as the attacker's swap input). This is a direct, unconditional loss of user funds meeting Critical/High Sherlock thresholds.

## Likelihood Explanation

The native-ETH multicall pattern is explicitly documented and tested in this repository. Users following `multicall{value}(exactInput*)` without appending `refundETH` will strand ETH — a common integration mistake. An attacker can passively monitor the router's ETH balance on-chain and exploit any non-zero residue with a single unprivileged call (`exactInputSingle` with `tokenIn=WETH`, `msg.value=0`, zero WETH allowance). No special role, no malicious setup, and no flash loan is required. The attack is repeatable as long as ETH is stranded.

## Recommendation

Track the ETH that belongs to the current multicall context separately from any pre-existing router balance. One approach: record `address(this).balance` at the start of each `multicall` invocation and pass the delta (ETH added by the current call) as the usable native budget into `pay()`. Alternatively, require callers to pass `msg.value` explicitly through the call stack into `pay()` rather than reading `address(this).balance`, so only ETH sent in the current transaction is eligible for wrapping.

## Proof of Concept

```
1. Victim calls:
     router.multicall{value: 1 ether}([
         exactInputSingle(tokenIn=WETH, amountIn=0.3 ether, ...)
     ])
   // refundETH omitted → 0.7 ETH stranded on router

2. Attacker observes router.balance == 0.7 ETH on-chain.

3. Attacker calls (msg.value = 0, zero WETH allowance):
     router.exactInputSingle(
         pool=<WETH/token1 pool>,
         tokenIn=WETH,
         amountIn=0.7 ether,
         amountOutMinimum=0,
         recipient=attacker,
         ...
     )

4. Pool calls metricOmmSwapCallback → _justPayCallback →
     pay(WETH, attacker, pool, 0.7 ether)
       nativeBalance = address(this).balance = 0.7 ether >= 0.7 ether
       → WETH.deposit{value: 0.7 ether}()   // victim's ETH wrapped
       → WETH.transfer(pool, 0.7 ether)     // sent to pool
       // no safeTransferFrom on attacker — attacker pays nothing

5. Attacker receives token1 output worth ~0.7 ETH.
   Victim's 0.7 ETH is permanently lost.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
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
