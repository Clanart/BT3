Audit Report

## Title
Stranded ETH from a prior multicall is silently consumed by any subsequent WETH swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` uses `address(this).balance` as the first-priority funding source whenever `token == WETH`, with no per-caller accounting. Because `multicall` is `payable` and `refundETH` is an optional, separate call, ETH left over from one user's multicall sits unguarded in the router. The next WETH-input swap by any caller wraps and spends that ETH instead of pulling WETH from the new caller's wallet, permanently destroying the original depositor's funds.

## Finding Description
`exactInputSingle` (and every other swap entry-point) is `payable` and records `msg.sender` as `payer` in transient storage via `_setNextCallbackContext`. [1](#0-0) 

The pool calls back into `metricOmmSwapCallback` → `_justPayCallback` → `pay(tokenIn, payer=msg.sender, pool, value)`. [2](#0-1) 

Inside `pay`, the WETH branch reads the **global** router balance with no per-caller slot:

```solidity
uint256 nativeBalance = address(this).balance;   // entire router balance
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
} else if (nativeBalance > 0) { ... }
``` [3](#0-2) 

Any ETH sitting in the router — regardless of who deposited it — is treated as available collateral for the current swap. `refundETH` returns the entire balance to `msg.sender`, but it is a separate, optional call that users must explicitly append to their multicall: [4](#0-3) 

`multicall` itself imposes no requirement that ETH is fully consumed before returning: [5](#0-4) 

## Impact Explanation
Direct, permanent loss of ETH principal for any user who sends more ETH than their swap consumes and omits `refundETH`. The attacker pays zero WETH from their own wallet; the victim's ETH is wrapped and forwarded to the pool on the attacker's behalf. The attacker receives full swap output; the victim receives nothing in return for the stolen ETH. This is a **High** severity direct loss of user principal.

## Likelihood Explanation
- Users are expected to call `refundETH` as best practice, but the router provides no enforcement.
- A single `exactInputSingle{value: X}(...)` call (not wrapped in multicall) with any excess ETH strands funds immediately with no refund path.
- An attacker can monitor the mempool or simply call after any block where the router holds ETH, issuing a WETH swap sized to exactly the stranded amount, costing only gas.
- Likelihood is **Medium**: requires a victim mistake (omitting `refundETH`), but the exploit is trivially executable once the precondition exists.

## Recommendation
Track per-call ETH using a transient storage slot set to `msg.value` at each `payable` entry-point. In `pay`, consume only up to the amount the current caller deposited (i.e., snapshot `msg.value` into a transient slot at entry and subtract from it in `pay`, reverting if the router's balance exceeds what the current caller deposited). Alternatively, require `msg.value == 0` for non-ETH swaps and only allow the native ETH path when the current call is the one that deposited it.

## Proof of Concept
1. User A calls `router.multicall{value: 1 ether}([exactInputSingle(WETH→token, amountIn=0.9 ether, ...)])` — no `refundETH` appended. Swap succeeds; router now holds `0.1 ether`.
2. Attacker calls `router.exactInputSingle{value: 0}(ExactInputSingleParams{tokenIn: WETH, amountIn: 0.1 ether, ...})`.
3. Pool callback fires → `pay(WETH, attacker, pool, 0.1 ether)`.
4. `nativeBalance = address(this).balance = 0.1 ether >= value = 0.1 ether` → router wraps User A's ETH and transfers WETH to pool.
5. Attacker receives full swap output; `safeTransferFrom(attacker, ...)` is never reached.
6. `router.balance == 0`; User A's `0.1 ether` is gone with no recourse.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-71)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
