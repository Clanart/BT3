The code is fully traced. The vulnerability is real and exploitable. Here is the complete analysis:

---

### Title
Stranded ETH on Router Consumed by Subsequent Caller's WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` uses `address(this).balance` — the router's **total** native ETH balance — when settling a WETH-input swap. It does not restrict itself to the current call's `msg.value`. Any ETH left on the router from a prior `multicall` (user A forgot `refundETH`) is silently consumed to fund a subsequent caller's (user B's) WETH payment, giving user B a free swap and permanently stealing user A's ETH.

### Finding Description

**Root cause — `PeripheryPayments.pay` (lines 73–84):** [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` — the router's entire native balance — and uses it preferentially before pulling WETH from the payer via `transferFrom`. There is no accounting of which ETH belongs to which call.

**How ETH gets stranded:**

`multicall` is `payable` and uses `delegatecall`. [2](#0-1) 

The `receive()` guard only blocks plain ETH transfers, not ETH attached to a function call: [3](#0-2) 

So `multicall{value: Y}([exactInputSingle(amountIn=N)])` with `N < Y` and no `refundETH` step strands `Y − N` ETH on the router across transaction boundaries.

**Attack path through `exactInput`:**

For hop 0, the payer is set to `msg.sender` (user B) and the token is `params.tokens[0]` (WETH): [4](#0-3) 

The pool callback dispatches to `_justPayCallback`, which calls `pay(WETH, userB, pool, N)`: [5](#0-4) 

Inside `pay`, if `address(this).balance >= N` (satisfied by user A's stranded ETH), the router deposits that ETH as WETH and transfers it to the pool — user B's obligation is fully discharged using user A's funds: [6](#0-5) 

### Impact Explanation

- **User A** loses up to `Y − N` ETH (their stranded balance), permanently and silently.
- **User B** executes a full swap with `msg.value = 0` and zero WETH allowance, receiving output tokens for free.
- The same attack works through `exactInputSingle` and `exactOutputSingle` whenever `tokenIn == WETH`.
- Severity: **High** — direct loss of user principal with no recovery path once consumed.

### Likelihood Explanation

The standard usage pattern for ETH-input swaps is `multicall{value: X}([swap, refundETH])`. Any user who omits `refundETH` (e.g., sends exact amount but pool partially fills, or composes calls incorrectly) strands ETH. An attacker can monitor the mempool or router balance and immediately follow with a zero-value WETH exactInput call in the next block. No privileged access, no malicious pool, no non-standard token required.

### Recommendation

Track the ETH available for the **current call** separately from the router's persistent balance. The standard fix is to pass `msg.value` (or a per-call budget) into `pay` and cap native ETH usage to that amount:

```solidity
// In pay(), replace:
uint256 nativeBalance = address(this).balance;
// With:
uint256 nativeBalance = _msgValueBudget(); // transient, set at exactInput* entry, decremented on use
```

Alternatively, restrict WETH-from-ETH conversion to only occur when `msg.value > 0` in the current top-level call, and track it in transient storage alongside the existing callback context.

### Proof of Concept

```solidity
// 1. User A strands ETH on the router
vm.prank(userA);
router.multicall{value: 1 ether}(
    [abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        pool: pool, tokenIn: WETH, tokenOut: token1,
        zeroForOne: true, amountIn: 0.5 ether, amountOutMinimum: 0,
        recipient: userA, deadline: block.timestamp + 1, priceLimitX64: 0, extensionData: ""
    })))]
);
// 0.5 ETH stranded on router (no refundETH call)
assertEq(address(router).balance, 0.5 ether);

// 2. User B calls exactInput with WETH tokenIn, zero msg.value, zero WETH allowance
vm.prank(userB);
// userB has NO ETH sent and NO WETH approval
uint256 amountOut = router.exactInput(ExactInputParams({
    tokens: [WETH, token1], pools: [pool], extensionDatas: [""],
    zeroForOneBitMap: 1, amountIn: 0.5 ether, amountOutMinimum: 0,
    recipient: userB, deadline: block.timestamp + 1
}));

// User B received tokens for free; user A's 0.5 ETH is gone
assertGt(amountOut, 0);                          // userB got tokens
assertEq(address(router).balance, 0);            // stranded ETH consumed
assertEq(weth.balanceOf(userB), 0);              // userB spent no WETH
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
