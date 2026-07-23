Audit Report

## Title
Stranded native ETH on the router is silently consumed to subsidize a later user's WETH input payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` uses `address(this).balance` — the router's total ETH balance — to partially fund a WETH input payment without verifying that the ETH belongs to the current payer or was sent in the current transaction. ETH left on the router from a prior user's payable call (who omitted `refundETH()`) is silently consumed to reduce the WETH pulled from a subsequent caller.

## Finding Description
`exactInputSingle` is `payable` and does not automatically refund excess ETH. When a user calls `exactInputSingle{value: X}(...)` with `tokenIn = WETH` and `amountIn < X`, the `pay()` callback fires with `value = amountIn`. Because `address(this).balance = X >= amountIn`, the `nativeBalance >= value` branch deposits exactly `amountIn` ETH and the remaining `X - amountIn` ETH stays on the router. [1](#0-0) 

A subsequent caller who swaps WETH as `tokenIn` triggers `_justPayCallback` → `pay(WETH, msg.sender, pool, value)`. If `0 < address(this).balance < value`, the partial-native branch fires: [2](#0-1) 

The stranded ETH is deposited and forwarded to the pool, and only `value - nativeBalance` is pulled from the subsequent caller via `safeTransferFrom`. There is no guard tying `nativeBalance` to the current `msg.sender` or the current transaction's `msg.value`. The `receive()` guard (rejecting non-WETH ETH senders) does not prevent ETH from accumulating via payable entry points. [3](#0-2) 

The call chain is: `exactInputSingle` (sets payer = `msg.sender`) → pool `swap` → `metricOmmSwapCallback` → `_justPayCallback` → `pay`. [4](#0-3) [5](#0-4) 

## Impact Explanation
Direct loss of user principal. The victim permanently loses the ETH they sent to the router that was not consumed by their own swap. The subsequent caller (attacker) receives a discount on their WETH input equal to the stranded amount. This satisfies the "direct loss of user principal" threshold at Medium severity.

## Likelihood Explanation
Any user who calls a payable swap function with excess ETH and omits `refundETH()` creates the precondition. The test suite confirms the correct pattern requires an explicit `refundETH()` call in a `multicall`, but nothing enforces this at the contract level. [6](#0-5) 

An attacker can monitor the router's ETH balance on-chain and immediately follow with a WETH swap to drain it. The attack is repeatable and requires no special privileges.

## Recommendation
Track how much ETH the current transaction deposited (e.g., store `msg.value` at the top of each payable entry point in transient storage) and cap the native ETH consumed in `pay` to that amount. Alternatively, pass the caller-supplied ETH amount explicitly through the call stack rather than reading the ambient `address(this).balance`.

## Proof of Concept
```solidity
// Step 1: victim strands ETH on the router
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 0.4 ether,   // only 0.4 ETH consumed; 0.6 ETH stranded
    // ... no refundETH() call
}));
// address(router).balance == 0.6 ether

// Step 2: attacker exploits stranded ETH
uint256 wethBefore = weth.balanceOf(attacker);
router.exactInputSingle(ExactInputSingleParams({
    tokenIn: address(weth),
    amountIn: 1 ether,     // pay() sees nativeBalance=0.6 > 0, < 1 ETH
    // ...
}));
// attacker's WETH spent == 0.4 ether (not 1 ether)
// victim's 0.6 ETH is gone
assert(wethBefore - weth.balanceOf(attacker) == 0.4 ether);
assert(address(router).balance == 0);
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-84)
```text
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
