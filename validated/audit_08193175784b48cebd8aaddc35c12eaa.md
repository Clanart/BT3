Looking at the actual code to verify the claim:

Audit Report

## Title
Stale Router ETH Balance Consumed by Subsequent User's WETH Payment — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
The `pay()` function in `PeripheryPayments` reads `address(this).balance` — the router's entire native ETH balance — when settling a WETH obligation, with no check that the ETH belongs to the current transaction. ETH stranded on the router by a prior user (who sent excess `msg.value` without calling `refundETH()`) is silently consumed by any subsequent WETH-input swap, causing the original depositor to permanently lose their ETH while the later user pays nothing from their own wallet.

## Finding Description
`exactInputSingle` is declared `external payable`, so callers can send ETH with it directly without a multicall wrapper. [1](#0-0) 

When `tokenIn == WETH`, the pool's swap callback triggers `_justPayCallback` → `pay(WETH, payer, pool, amount)`. [2](#0-1) 

Inside `pay()`, the WETH branch unconditionally reads `address(this).balance` and uses it to cover the obligation before pulling from the payer's wallet: [3](#0-2) 

There is no mechanism to distinguish ETH that arrived in the current transaction from ETH left over from a prior transaction. The `receive()` guard only blocks plain ETH transfers; it does not prevent ETH from accumulating via `msg.value` on payable function calls. [4](#0-3) 

The intended usage pattern requires pairing ETH-input swaps with `refundETH()` inside a multicall, as shown in the test suite: [5](#0-4) 

However, because `exactInputSingle` is directly callable as a payable function, a user who calls it outside a multicall — or who omits `refundETH()` — strands the surplus ETH on the router across transaction boundaries. Any subsequent WETH-input swap by any user then consumes that stranded balance, fully satisfying their payment obligation without touching their wallet.

## Impact Explanation
Direct loss of user principal: User A's stranded ETH is permanently consumed by User B's swap. User A receives no compensation and has no recovery path. User B receives the full swap output without spending any WETH or ETH. The pool receives the correct WETH amount and is unaware of the misattribution. Loss magnitude equals the stranded ETH amount, which is unbounded (any excess `msg.value` not refunded). This constitutes a direct, irreversible loss of user funds meeting Critical/High severity thresholds.

## Likelihood Explanation
The `exactInputSingle` (and `exactOutputSingle`, `exactInput`, `exactOutput`) functions are all `payable` and publicly callable without a multicall. Any user or integrator who calls them directly with excess ETH — a natural pattern given the payable signature — will strand ETH. No special permissions, front-running, or privileged access are required for the attacker: any subsequent WETH-input swap by any unprivileged user in any later block drains the stranded balance. The condition is repeatable and requires no coordination.

## Recommendation
Track only the ETH that arrived in the current transaction as eligible for WETH conversion. The standard approach is to record `msg.value` at the multicall entry point and pass it explicitly through the call stack, decrementing it as it is consumed by `pay()`. Alternatively, compare `address(this).balance` before and after the top-level call and restrict native-ETH-to-WETH conversion to only the delta. This ensures that ETH from prior transactions cannot be attributed to a new caller's obligation.

## Proof of Concept
```
Setup:
  - Router deployed with WETH address.
  - Pool with WETH as token0, token1 as ERC20.
  - User A has 2000 wei ETH.
  - User B has 0 ETH, 0 WETH, but wants to swap 1000 WETH → token1.

Step 1 (User A, Tx 1):
  User A calls exactInputSingle{value: 2000}(amountIn=1000, tokenIn=WETH, ...)
  → pay() sees nativeBalance=2000 >= 1000, deposits 1000 ETH as WETH, pays pool.
  → 1000 ETH remains on router (User A did not call refundETH()).

Step 2 (User B, Tx 2):
  User B calls exactInputSingle{value: 0}(amountIn=1000, tokenIn=WETH, ...)
  → pay(WETH, UserB, pool, 1000) is called.
  → nativeBalance = 1000 (User A's stranded ETH).
  → nativeBalance >= value: deposits 1000 ETH as WETH, transfers to pool.
  → User B's WETH balance: unchanged (0 spent).
  → User B receives token1 output.

Result:
  User A: lost 1000 ETH permanently.
  User B: received token1 output for free.
  Pool: received correct WETH, unaware of misattribution.
```

Foundry test: deploy router and pool, deal User A 2000 wei, call `exactInputSingle{value: 2000}` with `amountIn=1000` from User A (no multicall, no `refundETH`), assert `address(router).balance == 1000`. Then call `exactInputSingle{value: 0}` with `amountIn=1000` from User B (zero WETH balance, zero ETH), assert User B receives token1 output and `address(router).balance == 0`.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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
