Audit Report

## Title
Stranded ETH on router consumed to fund subsequent user's WETH `exactInput` payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` uses `address(this).balance` — the router's entire native ETH balance — when wrapping ETH to WETH on behalf of an external payer. ETH left on the router from a prior `multicall` overpayment (without `refundETH`) is silently consumed to fund a later caller's WETH swap, permanently stealing the prior user's ETH without touching the later caller's WETH balance or allowance.

## Finding Description
**Root cause — `pay()` uses ambient balance:**

In `PeripheryPayments.pay()`, when `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` with no restriction to ETH sent in the current transaction: [1](#0-0) 

**ETH accumulation path:**

`multicall` is `payable` and accepts any ETH: [2](#0-1) 

The `receive()` guard only blocks direct plain-ETH sends from non-WETH addresses; it does not prevent ETH from accumulating via `multicall{value: ...}`: [3](#0-2) 

**Exploit path:**

1. User A calls `multicall{value: 2 ether}([exactInputSingle(tokenIn=WETH, amountIn=1 ether, ...)])` without appending `refundETH()`. `pay()` wraps 1 ETH for the swap; 1 ETH remains stranded on the router.

2. User B calls `exactInput{value: 0}(tokens=[WETH, token1], pools=[p1], amountIn=1 ether)`. For hop 0, the callback context is set with `payer = msg.sender` (User B) and `token = WETH`: [4](#0-3) 

3. The pool fires `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, UserB, pool, 1 ether)`: [5](#0-4) 

4. Inside `pay()`, `nativeBalance = address(this).balance = 1 ether` (User A's stranded ETH). Since `nativeBalance >= value`, the router wraps that ETH and transfers WETH to the pool — User B's WETH balance and allowance are never touched. User A's 1 ETH is permanently gone.

## Impact Explanation
Direct loss of user principal: User A's stranded ETH is consumed to pay for User B's swap. User A receives nothing in return and has no recovery path. The loss is proportional to the stranded amount, up to the full `amountIn` of User B's swap. This is a High-severity direct fund loss from a standard router interaction.

## Likelihood Explanation
ETH stranding is a routine operational condition: users commonly overpay native ETH in `exactInputSingle` or `exactInput` with `tokenIn=WETH` and omit `refundETH()` from their multicall. Any subsequent WETH-tokenIn swap by any user will silently drain the stranded balance. No special permissions, malicious setup, or non-standard tokens are required.

## Recommendation
Replace the ambient `address(this).balance` check with only the ETH attributable to the current call. Options:
- Pass `msg.value` down through the call stack and use it as the upper bound for ETH wrapping in `pay()`.
- Record `address(this).balance` at the top of each entry-point (`exactInput`, `exactInputSingle`, etc.) before any pool interaction, and restrict WETH wrapping in `pay()` to that snapshot.
- When `payer != address(this)`, always pull WETH via `safeTransferFrom(payer, ...)` and handle native ETH wrapping only when `msg.value > 0` in the same call context.

## Proof of Concept
```solidity
// 1. User A strands 1 ETH on the router
router.multicall{value: 2 ether}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        tokenIn: WETH, amountIn: 1 ether, ...
    })))
    // no refundETH — 1 ether stranded on router
]);

// 2. User B swaps WETH->token1 with zero msg.value and zero WETH allowance
router.exactInput{value: 0}(ExactInputParams({
    tokens: [WETH, token1],
    pools: [p1],
    amountIn: 1 ether,
    ...
}));
// Assert: swap succeeds, User B receives token1
// Assert: User B's WETH balance and allowance are unchanged
// Assert: User A's 1 ETH is permanently gone from the router
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-103)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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
