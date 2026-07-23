### Title
ETH Permanently Trapped in Router and LiquidityAdder When `msg.value` Sent for Non-WETH Token Swaps/Deposits — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

Every user-facing entry point in `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` is declared `payable`, yet none of them revert when `msg.value > 0` and the input token is not WETH. Any ETH sent alongside a non-WETH swap or liquidity deposit is silently accepted by the contract and permanently locked, because the `pay` callback path is driven entirely by the token address stored in transient context — it never touches `msg.value` unless that token resolves to WETH.

---

### Finding Description

**`MetricOmmSimpleRouter`** exposes four `payable` swap entry points: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Each sets a transient callback context that records the **token to pay** (`params.tokenIn` / `params.tokens[0]`): [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

When the pool calls back, `_justPayCallback` (or `_exactOutputIterateCallback`) calls `pay` with that stored token: [9](#0-8) [10](#0-9) 

`pay` (inherited from `PeripheryPayments`) wraps ETH only when the token address equals WETH. When `tokenIn` is any other ERC-20 (e.g., USDC, DAI), `pay` calls `transferFrom` on the payer and **never consumes `msg.value`**. The ETH is left in the contract with no automatic refund path.

The same pattern applies to **`MetricOmmPoolLiquidityAdder`**: [11](#0-10) [12](#0-11) [13](#0-12) 

In the liquidity callback, payment is made using `token0` and `token1` read from pool immutables: [14](#0-13) 

If neither pool token is WETH, any ETH sent with the call is trapped.

Both contracts also expose a `payable multicall`: [15](#0-14) [16](#0-15) 

`multicall` is `payable` to support batching a WETH-input swap with a `refundETH` call. However, if a user calls `multicall` with ETH and the batched swap uses a non-WETH token (or forgets to append `refundETH`), the ETH is permanently lost.

---

### Impact Explanation

ETH sent by a user alongside a non-WETH swap or liquidity deposit is permanently locked in the router or liquidity adder contract. There is no automatic refund, no revert, and no recovery mechanism for the trapped ETH. This constitutes a **direct, irreversible loss of user principal**.

---

### Likelihood Explanation

- Users familiar with Uniswap v3-style routers (which also accept ETH for WETH swaps) may habitually send ETH when interacting with any `payable` router function.
- Wallet UIs and scripts that auto-populate `msg.value` for `payable` functions increase the risk.
- The `multicall` pattern specifically encourages sending ETH upfront (for WETH wrapping), making it easy to accidentally include ETH in a non-WETH batch.
- No on-chain guard exists to catch the mistake.

Likelihood: **Medium** (user error is plausible; no guard prevents it).

---

### Recommendation

Add a guard at the top of every non-WETH-input entry point:

```solidity
// In exactInputSingle, exactInput, exactOutputSingle, exactOutput:
if (params.tokenIn != WETH9 && msg.value > 0) revert UnexpectedETH();

// In addLiquidityExactShares / addLiquidityWeighted:
PoolImmutables memory imm = IMetricOmmPool(pool).getImmutables();
if (imm.token0 != WETH9 && imm.token1 != WETH9 && msg.value > 0) revert UnexpectedETH();
```

Alternatively, ensure a `refundETH` function exists and is prominently documented as mandatory when ETH is sent, and add a `msg.value == 0` assertion for all non-WETH paths.

---

### Proof of Concept

1. User wants to swap USDC → TOKEN via `exactInputSingle` with `tokenIn = USDC`.
2. User mistakenly sends `1 ETH` with the call (e.g., wallet pre-fills `msg.value`).
3. `exactInputSingle` is `payable` — the call succeeds.
4. Transient context records `tokenIn = USDC`.
5. Pool calls `metricOmmSwapCallback`; `_justPayCallback` calls `pay(USDC, payer, pool, amount)`.
6. `pay` executes `IERC20(USDC).transferFrom(payer, pool, amount)` — ETH is untouched.
7. Swap completes successfully. The `1 ETH` remains in `MetricOmmSimpleRouter` with no recovery path.
8. The user has lost `1 ETH` permanently. [17](#0-16) [9](#0-8)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-92)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-103)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-135)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-154)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L162-163)
```text
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L207-213)
```text
    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L42-47)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-177)
```text
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
