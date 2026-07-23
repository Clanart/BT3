Audit Report

## Title
Stranded native ETH on the router is silently consumed by any subsequent WETH swap from an unrelated caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` funds WETH payments by reading `address(this).balance` without any per-caller attribution. If a prior caller left ETH on the router (e.g., by sending excess `msg.value` and omitting `refundETH()`), the next caller who swaps with `tokenIn == WETH` will have their payment funded from that stranded ETH — requiring no WETH approval or ETH contribution of their own. The original depositor's ETH is irrecoverably lost.

## Finding Description
`pay()` in `PeripheryPayments.sol` handles WETH payments by reading the router's entire native balance: [1](#0-0) 

The design assumes any ETH on the router belongs to the current caller. This assumption is broken across transaction boundaries. Every swap entry-point is `payable`: [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` in payable calls: [3](#0-2) 

`refundETH()` is a separate, optional call: [4](#0-3) 

**Exploit path:**
1. Alice calls `exactInputSingle{value: 1 ether}(...)` with `amountIn = 0.5 ether`, `tokenIn = WETH`. `pay()` deposits 0.5 ETH as WETH; 0.5 ETH remains on the router unattributed.
2. Bob calls `exactInputSingle{value: 0}(...)` with `amountIn = 0.5 ether`, `tokenIn = WETH`, no WETH approval. `pay()` reads `nativeBalance = 0.5 ether >= value = 0.5 ether`, deposits Alice's ETH as WETH, and transfers it to the pool on Bob's behalf.
3. Bob receives the output tokens; Alice's 0.5 ETH is gone with no recovery path.

No privileged access is required. The callback context correctly identifies `payer = msg.sender` (Bob), but `pay()` never checks whether `msg.value` in the current transaction covers the requested amount before consuming the router's full native balance. [5](#0-4) 

## Impact Explanation
Direct, irrecoverable loss of user ETH. The stranded ETH is transferred to the pool as WETH on behalf of an unrelated caller's swap. This meets the Sherlock High threshold: unprivileged attacker, no special setup, direct principal loss bounded only by the stranded amount and the attacker's chosen `amountIn`.

## Likelihood Explanation
Medium. Sending a round-number `msg.value` buffer when swapping WETH is a common pattern. Omitting `refundETH()` — by user error, a frontend bug, or a failed second call in a `multicall` — leaves ETH stranded. A MEV bot watching the mempool can drain it in the same or next block with a zero-value WETH swap requiring no approvals.

## Recommendation
Track the ETH contributed by the current caller in transient storage at the start of each payable entry-point (e.g., store `msg.value` in a transient slot keyed to the call). In `pay()`, cap native-ETH consumption to that tracked amount rather than `address(this).balance`. Alternatively, require `msg.value` to equal exactly the WETH `amountIn` when `tokenIn == WETH` and revert otherwise, or remove the native-ETH shortcut entirely and require callers to pre-wrap ETH before calling the router.

## Proof of Concept
```solidity
// Step 1 — Alice strands 0.5 ETH
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountIn: 0.5 ether,
    amountOutMinimum: 0, recipient: alice,
    deadline: block.timestamp, priceLimitX64: 0, extensionData: ""
}));
// 0.5 ETH remains on router; Alice did not call refundETH()

// Step 2 — Bob drains Alice's ETH with no ETH or WETH approval
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountIn: 0.5 ether,
    amountOutMinimum: 0, recipient: bob,
    deadline: block.timestamp, priceLimitX64: 0, extensionData: ""
}));
// pay() sees nativeBalance=0.5e >= value=0.5e → deposits Alice's ETH, Bob gets token1 output
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```
