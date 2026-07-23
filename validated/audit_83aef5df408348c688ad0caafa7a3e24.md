The code is clear. Let me trace the exact execution path to confirm the vulnerability.

**Execution trace for the claimed attack:**

1. User A calls `multicall{value: Y}([exactInputSingle(..., tokenIn=WETH, amountIn=N)])` where `Y > N` and omits `refundETH()`. After the call, `Y - N` ETH is stranded on the router.

2. User B calls `exactInput{value: 0}(tokens=[WETH, token1], pools=[p1], amountIn=N)`.

3. In `exactInput`, hop 0 sets payer = `msg.sender` (User B), token = WETH, then calls `pool.swap()`. [1](#0-0) 

4. The pool fires `metricOmmSwapCallback`, which calls `_justPayCallback`, which calls `pay(WETH, UserB, pool, N)`. [2](#0-1) 

5. In `pay`, since `token == WETH` and `payer != address(this)`, it reads `nativeBalance = address(this).balance`. If `nativeBalance >= N` (i.e., User A's stranded ETH covers it), the router deposits that ETH as WETH and transfers it to the pool — **without pulling any WETH from User B**. [3](#0-2) 

The `pay` function has no mechanism to distinguish between ETH sent in the current call (`msg.value`) and ETH stranded from prior calls. The `receive()` guard only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from accumulating via `multicall{value: ...}`. [4](#0-3) 

The vulnerability is real and exploitable.

---

### Title
Stranded ETH on router consumed to fund subsequent user's WETH `exactInput` payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay()` uses the router's entire native ETH balance when paying WETH on behalf of an external payer. ETH stranded from a prior `multicall` (e.g., overpayment without `refundETH`) is silently consumed to fund a later caller's WETH swap, stealing the prior user's ETH.

### Finding Description
In `pay()`, when `token == WETH` and `payer != address(this)`, the function checks `address(this).balance` and, if sufficient, wraps that ETH and sends it to the pool — never pulling from `payer`'s WETH allowance:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer never charged
    } ...
``` [3](#0-2) 

`address(this).balance` includes ETH from any prior `multicall{value: ...}` that was not fully consumed and not refunded. The `receive()` guard does not prevent this accumulation — it only blocks plain ETH transfers from non-WETH addresses; ETH sent as part of a `multicall` call is accepted normally. [4](#0-3) 

### Impact Explanation
User A's stranded ETH is permanently consumed to pay for User B's WETH swap. User A receives nothing in return. The loss is direct and proportional to the stranded amount, up to the full `amountIn` of User B's swap. This meets the High threshold: direct loss of user principal with no recovery path.

### Likelihood Explanation
ETH stranding is a normal operational condition: users routinely overpay native ETH in `exactInputSingle` or `exactInput` with WETH tokenIn and omit `refundETH()` from their multicall. Any subsequent WETH-tokenIn swap by any user will silently drain the stranded balance. No special permissions or malicious setup are required.

### Recommendation
Track only the ETH sent in the current call. Replace the ambient `address(this).balance` check with `msg.value` (passed down through the call stack), or record the pre-call balance at entry and limit WETH wrapping to `msg.value` only. Alternatively, require that WETH payments always pull from `payer` via `transferFrom` when `payer != address(this)`, and handle native ETH wrapping only when `msg.value > 0` in the same transaction context.

### Proof of Concept
```solidity
// 1. User A strands ETH on the router
router.multicall{value: 2 ether}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        tokenIn: WETH, amountIn: 1 ether, ...
    })))
    // no refundETH — 1 ether stranded
]);

// 2. User B swaps WETH->token1 with zero msg.value and zero WETH allowance
router.exactInput{value: 0}(ExactInputParams({
    tokens: [WETH, token1],
    pools: [p1],
    amountIn: 1 ether,
    ...
}));
// Assert: swap succeeds, User B receives token1, User A's 1 ETH is gone
// Assert: User B's WETH balance and allowance are unchanged
```

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```
