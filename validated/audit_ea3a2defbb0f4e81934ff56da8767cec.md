Audit Report

## Title
Unguarded Shared ETH Balance in `pay()` Lets Any Caller Drain Stranded Native ETH from the Router — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` as a global, unguarded pool and uses it to settle any WETH-input swap without verifying the current caller contributed that ETH. ETH stranded in the router from a prior caller's incomplete `multicall` (missing `refundETH()`) can be consumed by an attacker's subsequent WETH-input swap, giving the attacker output tokens for free and permanently stealing the prior user's ETH.

## Finding Description
In `pay()`, when `token == WETH` and `address(this).balance >= value`, the router wraps its own native ETH and transfers WETH to the pool — the `payer` address is never charged: [1](#0-0) 

The `payer` argument (set to `msg.sender` at swap entry via transient storage) is only consulted in the `else if (nativeBalance > 0)` and `else` branches. When the router's balance fully covers `value`, the payer is silently bypassed. [2](#0-1) 

ETH accumulates in the router whenever a `payable` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) is called with `msg.value` exceeding the amount consumed and the caller omits `refundETH()`. The `receive()` guard: [3](#0-2) 

only blocks direct ETH pushes from non-WETH addresses. It does **not** prevent ETH from accumulating via `msg.value` in `payable` functions, because `msg.value` is credited to the contract before `receive()` is ever consulted.

Exploit path:
1. User A calls `multicall{value: 1 ETH}([exactInputSingle(WETH→token1, amountIn=0.5 ETH)])` without appending `refundETH()`. During the callback, `pay()` wraps 0.5 ETH; 0.5 ETH remains stranded in the router.
2. Attacker calls `exactInputSingle(WETH→token1, amountIn=0.5 ETH)` with zero ETH sent and zero WETH approval.
3. Pool triggers `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, attacker, pool, 0.5 ETH)`.
4. `address(this).balance == 0.5 ETH >= 0.5 ETH` → router wraps its own ETH, sends WETH to pool.
5. Attacker receives token1 worth 0.5 ETH; User A's 0.5 ETH is permanently stolen. [4](#0-3) 

## Impact Explanation
Direct, permanent loss of user principal. Any ETH stranded in the router — a realistic outcome of the documented `multicall` + `refundETH` pattern when `refundETH` is omitted — can be atomically stolen by an unprivileged attacker. The attacker receives full swap output without paying any input. This is a swap conservation failure: the pool receives WETH (from the router's own balance) while the attacker's account is never debited, violating the invariant that the designated payer must fund every swap. Severity: **Critical**.

## Likelihood Explanation
The attack requires no special permissions, no token approvals, no privileged role, and no prior setup beyond observing a non-zero ETH balance in the router. Omitting `refundETH()` in a `multicall` is a common integration mistake, especially when callers send a round `msg.value` and let the swap consume only part of it. Any MEV bot monitoring `address(router).balance` on-chain can atomically exploit the window in the same block the ETH is stranded.

## Recommendation
Track per-transaction ETH contributions in transient storage rather than reading the global `address(this).balance`. At each `payable` entry point, record `msg.value` in a transient slot keyed to the current call. In `pay()`, consume only from that recorded per-call amount and fall through to `safeTransferFrom` if it is insufficient, forcing the remainder to be pulled from the payer. Alternatively, remove the native-ETH shortcut from `pay()` entirely and require callers to wrap ETH themselves (e.g., via a `wrapETH` helper in `multicall`) before invoking the router.

## Proof of Concept

```solidity
// Step 1 – User A leaves 0.5 ETH stranded in the router
router.multicall{value: 1 ether}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        pool:             pool,
        tokenIn:          WETH,
        tokenOut:         token1,
        zeroForOne:       true,
        amountIn:         0.5 ether,
        amountOutMinimum: 0,
        recipient:        userA,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })))
    // ← no refundETH(); 0.5 ETH stays in router
]);

// Step 2 – Attacker exploits the stranded ETH
// Attacker has zero WETH, sends zero ETH, has zero approval
router.exactInputSingle(ExactInputSingleParams({
    pool:             pool,
    tokenIn:          WETH,
    tokenOut:         token1,
    zeroForOne:       true,
    amountIn:         0.5 ether,   // <= router.balance
    amountOutMinimum: 0,
    recipient:        attacker,
    deadline:         block.timestamp + 1,
    priceLimitX64:    0,
    extensionData:    ""
}));
// Pool calls metricOmmSwapCallback → _justPayCallback → pay(WETH, attacker, pool, 0.5e18)
// address(this).balance == 0.5 ETH >= 0.5 ETH
// → router wraps its own ETH, sends WETH to pool
// → attacker receives token1 worth 0.5 ETH; User A's 0.5 ETH is gone
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
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
