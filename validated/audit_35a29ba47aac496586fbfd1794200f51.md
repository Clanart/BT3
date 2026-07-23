Audit Report

## Title
Stranded native ETH on the router is silently consumed by any subsequent WETH swap from an unrelated caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` without any per-caller attribution when handling WETH payments. Any native ETH left on the router from a prior transaction — due to a user sending excess `msg.value` without calling `refundETH()` — is silently consumed by the next caller who initiates a WETH swap, causing direct and irrecoverable loss of the original depositor's ETH.

## Finding Description
The root cause is in `pay()` at [1](#0-0)  — `address(this).balance` is read globally with no binding to the current transaction's `msg.value` or the current caller. The function assumes all ETH on the router belongs to the current caller, which is false across transaction boundaries.

Entry points like `exactInputSingle` are `payable` [2](#0-1) , so a user can send more ETH than `amountIn` requires. The `receive()` guard [3](#0-2)  only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` in payable calls.

`refundETH()` [4](#0-3)  is the intended recovery mechanism, but it is optional and must be explicitly included in a `multicall`. If omitted — by user error, a frontend bug, or a failed second call in a multicall — the surplus ETH persists on the router across blocks.

**Exploit path:**
1. Alice calls `exactInputSingle{value: 1 ether}` with `amountIn = 0.5 ether` and `tokenIn = WETH`, omitting `refundETH()`. `pay()` consumes 0.5 ETH; 0.5 ETH remains on the router.
2. Bob calls `exactInputSingle{value: 0}` with `amountIn = 0.5 ether` and `tokenIn = WETH`. In the swap callback, `_justPayCallback` calls `pay()` [5](#0-4)  with `payer = bob`, `token = WETH`, `value = 0.5 ether`.
3. `pay()` sees `nativeBalance = 0.5 ether >= value = 0.5 ether`, deposits Alice's ETH as WETH, and transfers it to the pool. [6](#0-5) 
4. Bob receives the token output; Alice's 0.5 ETH is gone with no recourse.

No WETH approval or ETH contribution is required from Bob.

## Impact Explanation
Direct, irrecoverable loss of user principal (native ETH). The stranded ETH is transferred to a pool as WETH on behalf of an attacker's swap. The loss is bounded only by the amount stranded and the attacker's chosen `amountIn`. This meets the contest's High-severity threshold for direct loss of user principal.

## Likelihood Explanation
Medium. Sending a round-number `msg.value` buffer and omitting `refundETH()` is a realistic user error and a known frontend integration pitfall. A MEV bot monitoring the mempool or pending state can detect stranded ETH and drain it in the very next block with a zero-cost WETH swap. The attack requires no special privileges, no approvals, and no capital beyond gas.

## Recommendation
Track the ETH contributed by the current caller in transient storage at the start of each payable entry-point (e.g., store `msg.value` in a transient slot keyed to the call). In `pay()`, cap native-ETH consumption to that tracked amount rather than `address(this).balance`. Alternatively, require `msg.value` to equal exactly the WETH `amountIn` and revert otherwise, eliminating the possibility of stranded ETH entirely.

## Proof of Concept
```solidity
// Step 1: Alice strands 0.5 ETH
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountIn: 0.5 ether,
    amountOutMinimum: 0, recipient: alice,
    deadline: block.timestamp, priceLimitX64: 0, extensionData: ""
}));
// router.balance == 0.5 ether (Alice's surplus, unrefunded)

// Step 2: Bob drains it with zero ETH and no WETH approval
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountIn: 0.5 ether,
    amountOutMinimum: 0, recipient: bob,
    deadline: block.timestamp, priceLimitX64: 0, extensionData: ""
}));
// pay() sees nativeBalance=0.5e >= value=0.5e → uses Alice's ETH
// Bob receives token1 output; Alice's 0.5 ETH is irrecoverably lost
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-78)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

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
