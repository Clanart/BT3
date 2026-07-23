### Title
Unvalidated `msg.value` in non-WETH swap and liquidity functions allows ETH to be stolen via `refundETH()` — (File: `metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

Every public swap entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) and every liquidity entry-point (`addLiquidityExactShares`, `addLiquidityWeighted`) is declared `payable` but never asserts `msg.value == 0` when the input token is not WETH. Any ETH attached to such a call is silently deposited to the router/adder's balance and is immediately claimable by any third party through the unrestricted `refundETH()` helper.

---

### Finding Description

`PeripheryPayments.pay()` consumes native ETH only when `token == WETH`:

```solidity
// PeripheryPayments.sol L73-87
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) { ... deposit + transfer ... }
    ...
} else {
    IERC20(token).safeTransferFrom(payer, recipient, value);   // ETH ignored
}
``` [1](#0-0) 

When `tokenIn` is any ERC-20 other than WETH, the `else` branch fires and `msg.value` is never touched. The ETH accumulates on the contract.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only intercepts bare ETH transfers with no calldata. It does **not** intercept `msg.value` attached to a named function call, so it provides no protection here. [2](#0-1) 

`refundETH()` is unrestricted: it sends the contract's entire ETH balance to whoever calls it:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no access control
    }
}
``` [3](#0-2) 

Affected `payable` entry-points with no `msg.value == 0` guard:

| Contract | Function | Line |
|---|---|---|
| `MetricOmmSimpleRouter` | `exactInputSingle` | 67 |
| `MetricOmmSimpleRouter` | `exactInput` | 92 |
| `MetricOmmSimpleRouter` | `exactOutputSingle` | 130 |
| `MetricOmmSimpleRouter` | `exactOutput` | 154 |
| `MetricOmmPoolLiquidityAdder` | `addLiquidityExactShares` (×2) | 56, 71 |
| `MetricOmmPoolLiquidityAdder` | `addLiquidityWeighted` (×2) | 88, 123 | [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) 

---

### Impact Explanation

A user who accidentally attaches ETH to a non-WETH swap or liquidity call loses that ETH permanently. The ETH is not returned by the swap logic, and any unprivileged address can immediately drain it by calling `refundETH()`. There is no time window in which the victim can recover the funds before an attacker does, because `refundETH()` is callable in the very next block (or even in the same block via a bot watching the mempool). The loss is direct principal loss with no protocol recourse.

---

### Likelihood Explanation

The functions are intentionally `payable` to support the ETH-input pattern (`multicall{value}(exactInputSingle(..., tokenIn=WETH, ...))`). Users who are familiar with this pattern may inadvertently attach ETH when switching to an ERC-20 input swap. Front-running bots routinely monitor for stranded ETH on well-known router addresses, so the window between the victim's transaction and theft is effectively zero.

---

### Recommendation

Add a `msg.value == 0` guard at the top of each function whose token path cannot consume native ETH. The cleanest approach is a shared modifier:

```solidity
modifier noNativeValue() {
    require(msg.value == 0, "unexpected msg.value");
    _;
}
```

Apply it to `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` when `tokenIn != WETH` is statically known, or unconditionally to `addLiquidityExactShares` and `addLiquidityWeighted` (the liquidity adder has no native-ETH consumption path at all). Alternatively, keep the functions `payable` but add the check inline before the swap/liquidity call, mirroring the 1inch recommendation:

```solidity
// before the swap when tokenIn is not WETH:
require(msg.value == 0, "unexpected msg.value");
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Assume: pool is a valid USDC/DAI pool (no WETH token).
// Victim accidentally sends 1 ETH with a USDC→DAI exactInputSingle call.

// Step 1 – Victim's transaction (accidental ETH attached):
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(usdcDaiPool),
        tokenIn:          address(usdc),   // NOT WETH
        tokenOut:         address(dai),
        zeroForOne:       true,
        amountIn:         1_000e6,
        amountOutMinimum: 0,
        recipient:        victim,
        deadline:         block.timestamp + 60,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// pay() takes the USDC via safeTransferFrom; the 1 ETH sits on the router.
// assert(address(router).balance == 1 ether);

// Step 2 – Attacker's transaction (any EOA, same or next block):
vm.prank(attacker);
router.refundETH();
// refundETH sends address(this).balance → msg.sender (attacker).
// assert(attacker.balance increased by 1 ether);
// assert(address(router).balance == 0);
```

The swap succeeds, the victim receives DAI, and the attacker receives the victim's 1 ETH. No privileged access is required; the only precondition is that the victim attached a nonzero `msg.value` to a non-WETH swap. [3](#0-2) [10](#0-9)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-87)
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-92)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-154)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```
