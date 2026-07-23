### Title
`addLiquidityExactShares` and `addLiquidityWeighted` are `payable` but silently trap ETH on non-WETH pools, enabling front-runner theft via `refundETH()` - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

---

### Summary

All four liquidity-adder entry points (`addLiquidityExactShares` × 2, `addLiquidityWeighted` × 2) are declared `payable`, yet the internal `pay()` helper only consumes native ETH when the pool token being paid is exactly `WETH`. For any pool whose token0 and token1 are both non-WETH ERC-20s, ETH sent with the call is silently left in the contract. Because `refundETH()` unconditionally forwards the entire contract ETH balance to `msg.sender`, a front-runner can call it immediately after the victim's transaction and steal the trapped ETH.

---

### Finding Description

`MetricOmmPoolLiquidityAdder` inherits `PeripheryPayments`, which provides a `receive()` that rejects direct ETH transfers from any address other than WETH: [1](#0-0) 

However, `receive()` does **not** gate ETH sent via a `payable` function call. All four liquidity entry points are declared `payable`: [2](#0-1) [3](#0-2) [4](#0-3) 

Inside the callback, `pay()` is the only place ETH is consumed, and it only does so when `token == WETH`: [5](#0-4) 

For any non-WETH pool token, the `else` branch at line 86 executes `safeTransferFrom`, completely ignoring `address(this).balance`. The ETH sent with the call accumulates in the contract.

`refundETH()` then sends the **entire** contract balance to whoever calls it: [6](#0-5) 

There is no access control and no binding to the original depositor. Any address that calls `refundETH()` after the victim's transaction receives the full trapped balance.

---

### Impact Explanation

A user who sends ETH alongside `addLiquidityExactShares` or `addLiquidityWeighted` on a non-WETH pool loses that ETH to the first caller of `refundETH()`. This is a **direct loss of user principal** with no recovery path for the victim once the front-runner has claimed the balance.

---

### Likelihood Explanation

- The interface NatSpec explicitly documents the ETH/multicall pattern for WETH pools, which trains users to send ETH with liquidity calls.
- A user who switches from a WETH pool to a non-WETH pool without changing their call pattern will silently lose ETH.
- `refundETH()` is a public, zero-argument function trivially callable by MEV bots monitoring the mempool.
- The `addLiquidityWeighted` probe-then-pay flow makes the window slightly wider: the probe reverts (no ETH consumed), then the paying add executes, leaving ETH in the contract throughout. [7](#0-6) 

---

### Recommendation

Remove `payable` from all four liquidity-adder entry points. ETH-for-WETH flows should be handled exclusively through `multicall{value: ...}([addLiquidityExactShares(...), refundETH()])`, where the `multicall` wrapper (which remains `payable`) is the sole ETH entry point. This matches the pattern already used by the swap router and eliminates the accidental-ETH trap entirely.

---

### Proof of Concept

```
// Pool: token0 = USDC, token1 = DAI (neither is WETH)

// Step 1 – Victim calls addLiquidityExactShares and accidentally sends 1 ETH
liquidityAdder.addLiquidityExactShares{value: 1 ether}(
    pool, owner, salt, deltas, maxAmount0, maxAmount1, ""
);
// pay() takes the safeTransferFrom path (token != WETH), ignores msg.value
// 1 ETH now sits in MetricOmmPoolLiquidityAdder

// Step 2 – Front-runner (Bob) sees the victim's tx in the mempool,
//           submits a higher-gas refundETH() call
liquidityAdder.refundETH();   // msg.sender = Bob
// _transferETH(Bob, 1 ether) executes; Bob receives victim's 1 ETH
```

The victim's liquidity is added correctly (ERC-20 pull succeeds), but the 1 ETH is permanently lost to the front-runner.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L71-81)
```text
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L15-17)
```text
/// @dev Native ETH input uses the same multicall pattern as the swap router: send ETH with the add call (or
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
```
