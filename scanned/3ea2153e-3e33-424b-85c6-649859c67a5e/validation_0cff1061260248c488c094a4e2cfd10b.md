### Title
Unattributed `address(this).balance` in `pay()` lets any caller drain stranded native ETH via `refundETH()` or consume it for free in a WETH swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` funds WETH swap callbacks by reading `address(this).balance` — the router's **total** native ETH balance — with no attribution to the current transaction's sender. When ETH is stranded on the router (e.g., from a partial-fill exact-input WETH swap or an over-sent `msg.value`), any subsequent caller can steal it outright via the public `refundETH()` function, or consume it for free through their own WETH swap callback.

---

### Finding Description

`pay()` in `PeripheryPayments.sol` handles the WETH leg of a swap callback as follows: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← total router ETH, not msg.value
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
}
```

`address(this).balance` is the **aggregate** ETH on the router, not the ETH contributed by the current call. There is no transient-storage slot or any other mechanism that records how much ETH the current transaction deposited.

**How ETH becomes stranded.** Every swap entry-point is `payable`: [2](#0-1) 

A user calling `exactInputSingle{value: X}` with `amountIn = X` and a non-zero `priceLimitX64` may receive a partial fill: the pool stops early and the callback is invoked with `actualAmountIn < X`. `pay()` deposits only `actualAmountIn` ETH as WETH and transfers it to the pool. The remaining `X − actualAmountIn` ETH stays on the router with no owner record.

The same stranding occurs for `exactOutputSingle` when `msg.value > actualAmountIn`, or whenever any payable function is called with surplus ETH.

**`receive()` does not prevent this.** It only rejects plain ETH transfers (no calldata) from non-WETH addresses; it does not intercept `msg.value` arriving inside a function call. [3](#0-2) 

**Theft vector 1 — `refundETH()`.** This public function sends the router's entire ETH balance to `msg.sender` with no access control: [4](#0-3) 

Any attacker who observes stranded ETH (via mempool or on-chain balance check) can call `refundETH()` in the next block and receive all of it.

**Theft vector 2 — free WETH swap.** An attacker calls `exactInputSingle{value: 0}` for a WETH→token swap. Inside the callback, `pay(WETH, attacker, pool, value)` is called. Because `address(this).balance` contains the victim's stranded ETH and `nativeBalance >= value`, the router deposits the victim's ETH as WETH and transfers it to the pool. The attacker receives tokens without spending anything.

**Theft vector 3 — `sweepToken` / `unwrapWETH9`.** Both are public with an arbitrary `recipient` parameter and operate on the router's full balance: [5](#0-4) 

Any WETH or ERC-20 tokens stranded on the router (e.g., from a swap with `recipient = address(router)` not followed by an unwrap in the same multicall) can be swept by any caller to any address.

---

### Impact Explanation

Direct loss of user principal. A victim who sends ETH for a WETH swap that partially fills loses the unspent ETH to the first attacker who calls `refundETH()`. Alternatively, the attacker executes a zero-cost WETH swap consuming the victim's ETH. The loss equals the stranded ETH amount, which can be arbitrarily large (bounded only by the victim's `msg.value`).

---

### Likelihood Explanation

Medium. The normal multicall pattern (`[exactInputSingle, refundETH]`) is atomic and leaves no residue. However:

- Users who call `exactInputSingle` or `exactOutputSingle` directly (not via multicall) with a price limit that causes a partial fill will strand ETH.
- Any user who over-estimates `msg.value` strands the excess.
- Attackers can watch the router's ETH balance in the mempool and front-run the victim's next transaction with a `refundETH()` call, or back-run the stranding transaction in the same block.

No privileged access is required; any EOA can trigger the theft.

---

### Recommendation

Track the ETH contributed by the current call in transient storage at each payable entry-point (e.g., `tstore(SLOT_MSG_VALUE, msg.value)`) and use only that recorded amount inside `pay()` instead of `address(this).balance`. Alternatively, enforce `msg.value == amountIn` for WETH exact-input swaps and revert on surplus, mirroring the pattern used by Uniswap v3's `SwapRouter` which stores `msg.value` in a transient slot and subtracts from it as ETH is consumed.

---

### Proof of Concept

```
Setup:
  - WETH/TOKEN pool exists and is registered on the factory.
  - Victim (Alice) holds 1000 ETH.
  - Attacker (Bob) holds 0 ETH.

Step 1 — Alice strands ETH:
  Alice calls:
    router.exactInputSingle{value: 1000}(ExactInputSingleParams{
        pool: pool,
        tokenIn: WETH,
        tokenOut: TOKEN,
        zeroForOne: true,
        amountIn: 1000,
        priceLimitX64: <tight limit that causes partial fill at 600>,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: ...
    })

  Pool partially fills: callback requests 600 ETH.
  pay(WETH, alice, pool, 600) → deposits 600 ETH as WETH, transfers to pool.
  Remaining 400 ETH stays on router (address(router).balance == 400).

Step 2a — Bob steals via refundETH:
  Bob calls:
    router.refundETH()
  → router sends 400 ETH to Bob.
  Alice loses 400 ETH.

Step 2b — (alternative) Bob steals via free WETH swap:
  Bob calls:
    router.exactInputSingle{value: 0}(ExactInputSingleParams{
        pool: pool,
        tokenIn: WETH,
        tokenOut: TOKEN,
        zeroForOne: true,
        amountIn: 300,
        amountOutMinimum: 0,
        recipient: bob,
        deadline: ...
    })
  Callback: pay(WETH, bob, pool, 300).
  address(this).balance == 400 >= 300 → deposits Alice's 300 ETH as WETH, transfers to pool.
  Bob receives TOKEN output without spending any ETH or WETH.
  Alice loses 300 ETH.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-55)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }

  /// @inheritdoc IPeripheryPayments
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
