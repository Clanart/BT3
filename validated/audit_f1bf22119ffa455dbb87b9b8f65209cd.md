Audit Report

## Title
`payable` liquidity-adder entry points silently trap ETH on non-WETH pools, enabling theft via `refundETH()` - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

## Summary
All four liquidity entry points in `MetricOmmPoolLiquidityAdder` are declared `payable`, but the internal `pay()` helper only consumes native ETH when the pool token is exactly `WETH`. For non-WETH pools, any ETH sent with the call is silently left in the contract. Because `refundETH()` unconditionally forwards the entire contract ETH balance to `msg.sender` with no access control, any caller — including a front-running MEV bot — can immediately drain the trapped ETH.

## Finding Description
All four entry points are `payable`: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Inside the callback, `pay()` is the sole ETH consumer. It only wraps native ETH when `token == WETH`; for any other token it falls through to the `else` branch and calls `safeTransferFrom`, leaving `msg.value` untouched in the contract: [5](#0-4) 

`refundETH()` then sends the full contract balance to whoever calls it, with no binding to the original depositor: [6](#0-5) 

The `receive()` guard only blocks direct ETH transfers from non-WETH addresses; it does not prevent ETH from entering via a `payable` function call: [7](#0-6) 

## Impact Explanation
A user who sends ETH alongside any of the four liquidity entry points on a non-WETH pool (e.g., USDC/DAI) loses that ETH permanently to the first caller of `refundETH()`. This is a direct loss of user principal with no recovery path. The impact qualifies as High/Critical under Sherlock thresholds: the victim's ERC-20 liquidity is added correctly, but the ETH is irretrievably stolen.

## Likelihood Explanation
The interface NatSpec explicitly documents the ETH/multicall pattern for WETH pools, training users to send ETH with liquidity calls. A user switching from a WETH pool to a non-WETH pool without changing their call pattern silently loses ETH. `refundETH()` is a public, zero-argument function trivially callable by MEV bots monitoring the mempool. The `addLiquidityWeighted` probe-then-pay flow widens the window: the probe reverts (no ETH consumed), then the paying add executes, leaving ETH in the contract for the entire duration of both calls. [8](#0-7) 

## Recommendation
Remove `payable` from all four liquidity-adder entry points. ETH-for-WETH flows should be handled exclusively through `multicall{value: ...}([addLiquidityExactShares(...), refundETH()])`, where the `multicall` wrapper (which remains `payable`) is the sole ETH entry point. This matches the pattern already used by the swap router and eliminates the accidental-ETH trap entirely.

## Proof of Concept
```solidity
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-64)
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L71-78)
```text
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-100)
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L123-134)
```text
  function addLiquidityWeighted(
    address pool,
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
```

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
