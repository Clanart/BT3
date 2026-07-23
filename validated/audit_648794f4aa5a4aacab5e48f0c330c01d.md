Audit Report

## Title
Router aggregate `address(this).balance` consumed by subsequent WETH swappers, enabling theft of stranded ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments::pay()` uses `address(this).balance` — the router's aggregate native ETH balance — to settle WETH payments, with no per-caller attribution. ETH stranded on the router when a user sends excess `msg.value` via a `payable` entry-point (e.g., `multicall`) and omits `refundETH()` is silently consumed by the next caller whose WETH payment triggers the native-balance branch, giving that caller a free or discounted swap at the victim's expense.

## Finding Description
`pay()` in `PeripheryPayments.sol` handles WETH payments with three branches keyed on `address(this).balance`:

```solidity
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
}
``` [1](#0-0) 

`address(this).balance` is the router's total native ETH, not the current caller's `msg.value`. ETH enters the router via `payable` entry-points (`multicall`, `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and WETH unwrapping; direct transfers are blocked by `receive()`. [2](#0-1) 

The `multicall` function is `payable` and performs no post-call check that `address(this).balance == 0`: [3](#0-2) 

After a multicall completes, any ETH the user sent but did not consume (and did not reclaim via `refundETH()`) remains on the router indefinitely. The partial-balance branch (lines 78–81) means **any positive residue**, however small, is consumed first before pulling from `payer`. A subsequent caller whose WETH payment triggers either branch will have their obligation partially or fully covered by the victim's stranded ETH.

No transient or persistent per-caller ETH accounting exists anywhere in `PeripheryPayments`, `MetricOmmSwapRouterBase`, or `MetricOmmSimpleRouter`. [4](#0-3) 

## Impact Explanation
Direct loss of user ETH principal. The victim loses the ETH they stranded on the router; the attacker receives a swap whose input cost is partially or fully covered by that ETH. Loss magnitude equals `msg.value_victim − amountIn_victim` per stranded transaction, which can be arbitrarily large. No privileged access is required; any address can call `exactInputSingle` or `exactInput` with `tokenIn = WETH`. [5](#0-4) 

## Likelihood Explanation
Medium. The precondition is that a user strands ETH on the router by omitting `refundETH()` from their multicall — a common mistake in Uniswap v3-style UX where the refund step is optional. An attacker can monitor on-chain state for a non-zero router ETH balance and exploit it in the very next block. The partial-balance branch means even a 1 wei residue is consumed, so the attacker does not need to wait for a large stranded amount. [6](#0-5) 

## Recommendation
Track the current transaction's consumed native ETH separately from `address(this).balance`. One approach: record `msg.value` at `multicall` entry in transient storage and decrement it as ETH is deposited into WETH inside `pay()`, using only that tracked value — not the aggregate balance — for WETH settlement. Alternatively, enforce that `refundETH()` is always the last call in any ETH-input multicall by checking at the end of `multicall` that `address(this).balance == 0` when `msg.value > 0`. [7](#0-6) 

## Proof of Concept
```
Setup:
  - Router deployed with WETH address.
  - Pool(WETH, token1) seeded with liquidity.

Step 1 — Victim strands ETH:
  victim calls multicall{value: 2 ether}([
      exactInputSingle(pool, tokenIn=WETH, amountIn=1 ether, ...)
      // NOTE: no refundETH() call
  ])
  // pay(): nativeBalance=2 ether >= value=1 ether → deposits 1 ether, transfers WETH to pool.
  // Remaining 1 ether stays on router.
  assert address(router).balance == 1 ether

Step 2 — Attacker exploits:
  attacker calls exactInputSingle(pool, tokenIn=WETH, amountIn=1 ether, ...)
  // pay(): nativeBalance=1 ether >= value=1 ether
  // Router deposits victim's 1 ether → WETH → pool.
  // Attacker's own WETH/ETH balance is NOT touched.
  // Attacker receives token1 output for free.
  assert address(router).balance == 0
  assert attacker_token1_balance > 0
  assert attacker_weth_balance unchanged

Step 3 — Victim tries to reclaim:
  victim calls refundETH()
  // address(this).balance == 0 → nothing sent back.
  // Victim has lost 1 ether.
```

The partial-balance branch makes this exploitable even when the stranded amount is less than the attacker's `amountIn`: the router contributes whatever ETH it holds and pulls only the shortfall from the attacker, giving the attacker a proportional discount funded entirely by the victim. [8](#0-7)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L57-63)
```text
  /// @inheritdoc IPeripheryPayments
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
