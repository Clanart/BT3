### Title
Unguarded `refundETH` and `sweepToken` drain any stranded native ETH or ERC-20 from the router to an arbitrary recipient, enabling theft of excess ETH deposited by another user's WETH swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH` and `sweepToken` are public with no access control and no attribution of who deposited the balance. The internal `pay` helper silently consumes any native ETH already sitting on the router when settling a WETH payment, regardless of which user deposited it. Because `exactInputSingle` and `exactInput` are `payable` and accept more ETH than `amountIn`, any excess ETH is stranded on the router after the swap. A watching attacker can immediately call `refundETH()` to steal it, or simply submit a zero-value WETH swap that is settled for free using the victim's ETH.

---

### Finding Description

`exactInputSingle` is `external payable`. [1](#0-0) 

Inside the pool callback, `pay` is called with `payer = msg.sender` and `token = WETH`. The first thing `pay` does is read `address(this).balance` and, if it is ≥ `value`, deposit that ETH as WETH and transfer it to the pool — without checking whether the ETH belongs to the current caller: [2](#0-1) 

After the swap returns, `_clearExpectedCallbackPool()` is called and the function exits. Any ETH sent above `amountIn` is never refunded; it remains on the router. [3](#0-2) 

`refundETH` is `external payable` with no guard — it sends the entire ETH balance to `msg.sender`: [4](#0-3) 

`sweepToken` is `public payable` with no guard — it sends the entire ERC-20 balance to a caller-chosen `recipient`: [5](#0-4) 

The `multicall` dispatcher on the router executes arbitrary `delegatecall`s with no ordering or access restrictions, so an attacker can compose `[sweepToken / refundETH]` as a standalone batch: [6](#0-5) 

---

### Impact Explanation

**Direct loss of user principal.** User A's excess ETH is consumed by User B's swap (User B pays nothing) or is swept outright by `refundETH`. The stolen amount equals the excess ETH User A sent above `amountIn`, which can be arbitrarily large. The pool receives the correct WETH amount in both cases, so the pool itself is unaffected; the loss falls entirely on User A.

---

### Likelihood Explanation

`exactInputSingle` is a standard public entry point. Users who call it directly (not via `multicall`) with `msg.value > amountIn` — a natural mistake when estimating gas or when the quoted amount changes — will strand ETH. A mempool-watching bot can detect the stranded balance in the same block and call `refundETH()` before the victim can recover it. No special privilege is required.

---

### Recommendation

1. At the end of `exactInputSingle` and `exactInput`, automatically refund any remaining `address(this).balance` to `msg.sender` (mirroring the `refundETH` pattern but making it mandatory).
2. Alternatively, track per-caller ETH deposits in transient storage and restrict `refundETH` to return only the caller's own deposited amount.
3. Add a `msg.value == 0 || token == WETH` guard so that ETH is only accepted when the input token is actually WETH, preventing accidental over-payment for ERC-20 swaps.

---

### Proof of Concept

**Theft via `refundETH`:**

```
// User A — sends 1000 ETH but only swaps 500
router.exactInputSingle{value: 1000}(ExactInputSingleParams({
    pool:            pool,
    tokenIn:         WETH,
    tokenOut:        token1,
    zeroForOne:      true,
    amountIn:        500,          // only 500 ETH deposited as WETH
    amountOutMinimum: 0,
    recipient:       userA,
    deadline:        block.timestamp + 1,
    priceLimitX64:   0,
    extensionData:   ""
}));
// 500 ETH now stranded on router

// Attacker — steals it in the next transaction
router.refundETH();   // receives 500 ETH, no approval or privilege needed
```

**Free swap via stranded ETH:**

```
// After User A's transaction above, 500 ETH is on the router.
// Attacker calls exactInputSingle with value = 0:
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool:            pool,
    tokenIn:         WETH,
    tokenOut:        token1,
    zeroForOne:      true,
    amountIn:        500,
    amountOutMinimum: 0,
    recipient:       attacker,
    ...
}));
// pay() sees address(this).balance == 500 >= value == 500
// deposits User A's ETH as WETH, pays the pool
// Attacker receives token1 output; User A's 500 ETH is gone
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L83-86)
```text
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
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
