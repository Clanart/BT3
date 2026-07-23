### Title
Excess Native ETH Sent for WETH-Input Swaps Is Not Automatically Refunded and Can Be Stolen by Any Caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

All swap entry-points in `MetricOmmSimpleRouter` are `payable`. When the input token is WETH, the `pay()` helper in `PeripheryPayments` wraps **exactly** the amount the pool requests from the contract's native ETH balance, leaving any excess ETH silently stranded in the router. No automatic refund is issued after the swap. The public `refundETH()` function sends the **entire** ETH balance to `msg.sender`, so any third party can call it in a subsequent transaction and steal the stranded ETH.

---

### Finding Description

**Step 1 – ETH enters the router.**

Every swap function (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) is `payable`. [1](#0-0) [2](#0-1) 

**Step 2 – `pay()` wraps only the exact amount the pool requests.**

Inside the swap callback, `_justPayCallback` calls `pay(tokenIn, payer, pool, amountToPay)`. When `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` and wraps **only** `value` wei, leaving any surplus untouched in the contract. [3](#0-2) 

**Step 3 – No refund after the swap.**

After the pool callback settles, the swap functions return without issuing any ETH refund. The excess ETH remains in the router. [4](#0-3) 

**Step 4 – `refundETH()` is permissionless and sends to `msg.sender`.**

The only recovery path sends the **entire** contract ETH balance to whoever calls it, not to the original depositor. [5](#0-4) 

A griever watching the mempool can front-run the victim's own `refundETH()` call, or simply call it in a later block, and receive the victim's excess ETH.

---

### Impact Explanation

**Direct loss of user principal.** A user who calls `exactOutputSingle` (or any swap) directly — not via a `multicall` bundle that appends `refundETH()` — and sends more ETH than the pool ultimately charges will permanently lose the difference. The loss is bounded only by `msg.value − amountIn`, which for exact-output swaps equals `amountInMaximum − actualAmountIn`. Any address can steal the stranded ETH by calling `refundETH()`.

---

### Likelihood Explanation

`exactOutputSingle` is the canonical use-case for sending excess ETH: the caller cannot know the exact input cost before execution and must supply up to `amountInMaximum`. Users who call the function directly (e.g., from a smart-contract integration, a script, or a wallet that does not compose a multicall) will routinely leave excess ETH in the router. The theft vector requires only a single permissionless call with no special privileges.

---

### Recommendation

Add an automatic ETH refund at the end of every `payable` swap function, mirroring the Uniswap v3 pattern:

```solidity
// at the end of exactOutputSingle / exactOutput / exactInputSingle / exactInput
uint256 excess = address(this).balance;
if (excess > 0) {
    _transferETH(msg.sender, excess);
}
```

Alternatively, enforce that callers always bundle swaps with `refundETH()` via `multicall`, and document this requirement prominently. The `refundETH()` function itself should also be restricted to send only to a caller-supplied `recipient` that was recorded at swap entry, preventing third-party theft.

---

### Proof of Concept

1. Alice wants to buy exactly 1 000 USDC for WETH. She does not know the exact WETH cost, so she calls:
   ```solidity
   router.exactOutputSingle{value: 1.1 ether}(ExactOutputSingleParams({
       pool: pool,
       recipient: alice,
       zeroForOne: true,          // WETH → USDC
       tokenIn: WETH,
       amountOut: 1000e6,
       amountInMaximum: 1.1 ether,
       ...
   }));
   ```
2. The pool charges 1.0 ETH. `pay()` wraps 1.0 ETH and transfers WETH to the pool. 0.1 ETH remains in the router.
3. `exactOutputSingle` returns. No refund is issued.
4. Bob (or a MEV bot) calls `router.refundETH()` and receives Alice's 0.1 ETH.

Alice loses 0.1 ETH with no recourse. [6](#0-5) [5](#0-4) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
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
