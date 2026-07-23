Audit Report

## Title
Stranded ETH on `MetricOmmPoolLiquidityAdder` Is Consumed as WETH Payment for Subsequent Callers, Causing Direct LP Principal Loss — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` uses `address(this).balance` as the source of native ETH when paying a WETH pool leg. This balance is contract-global, not per-caller. ETH left on the adder from any prior payable call is silently consumed for the next user's WETH liquidity payment, transferring the prior user's principal to the subsequent user's LP position.

## Finding Description
`pay()` reads the full contract ETH balance unconditionally when `token == WETH`: [1](#0-0) 

All four liquidity entry points are `payable`, so a caller can send ETH directly without a multicall wrapper: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

When a caller sends more ETH than the pool needs, `pay()` wraps exactly `value` and the remainder stays on the contract. The only recovery path is `refundETH()`, which must be explicitly included in a `multicall` — it is never called automatically: [6](#0-5) 

The `receive()` guard only blocks ETH from non-WETH senders, but does not prevent ETH accumulation from `msg.value` in payable calls: [7](#0-6) 

The transient pay context correctly records `payer` as the current caller, but `pay()` never checks whether `address(this).balance` was contributed by that `payer` — it consumes all available contract ETH unconditionally: [8](#0-7) 

## Impact Explanation
**HIGH — direct loss of user principal.** When Alice's stranded ETH is consumed for Bob's liquidity add, Alice permanently loses that ETH: it is wrapped and transferred to the pool as Bob's LP deposit. Bob receives a full or partial WETH subsidy at Alice's expense. The loss is bounded only by how much ETH Alice left stranded; in the worst case it equals the full `msg.value` she sent.

## Likelihood Explanation
**MEDIUM.** All four entry points are individually `payable`, inviting direct calls with ETH. Sending slightly more ETH than needed to avoid slippage reverts is a standard user pattern from Uniswap v2/v3 muscle memory. Any subsequent WETH-pool caller — including a bot watching the mempool — can drain the stranded balance in the very next block. No privileged access is required.

## Recommendation
Two complementary fixes:

1. **Track per-call ETH:** Record `msg.value` at entry and pass it as a cap into `pay()`, so only the current call's ETH is eligible for wrapping.
2. **Auto-refund excess ETH:** At the end of each non-multicall liquidity entry point, refund `address(this).balance` to `msg.sender` unconditionally (mirroring how Uniswap v3's `exactInput` handles ETH).

## Proof of Concept
```
Setup: WETH/token1 pool. Alice has 1 ETH. Bob has WETH approved.

1. Alice calls:
   adder.addLiquidityExactShares{value: 1 ether}(pool, alice, 1, delta, 1 ether, 0, "")
   Pool needs 0.6 ETH → pay() wraps 0.6 ETH, sends to pool.
   Remaining 0.4 ETH stays on adder (no refundETH called).

2. Bob calls (no ETH sent):
   adder.addLiquidityExactShares(pool, bob, 2, delta, 0.4 ether, 0, "")
   Pool needs 0.4 ETH → pay() sees nativeBalance = 0.4 ETH ≥ value,
   wraps Alice's 0.4 ETH, sends to pool as Bob's deposit.

Result:
- Alice lost 0.4 ETH (stranded ETH consumed for Bob's LP position).
- Bob paid 0 WETH from his own balance for 0.4 ETH worth of liquidity.
- adder.balance == 0 after Bob's call.
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L123-149)
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
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(msg.sender, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, msg.sender, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
