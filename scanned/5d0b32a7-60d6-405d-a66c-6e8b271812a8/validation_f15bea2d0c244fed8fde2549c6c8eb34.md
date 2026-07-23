### Title
Stuck ETH in payable router functions is silently consumed by any subsequent WETH-input swap caller via `pay()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` to subsidize WETH payments before pulling from the actual payer. Because every public entry point on both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` is `payable`, ETH can enter the contract without being consumed (e.g., a user sends ETH with a non-WETH swap). Any subsequent caller whose `tokenIn` is WETH will have their payment silently subsidized by that stranded ETH, effectively stealing it.

---

### Finding Description

`PeripheryPayments.pay()` contains the following WETH branch: [1](#0-0) 

When `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` and, if non-zero, wraps that native ETH and transfers it to the pool **before** (or instead of) pulling WETH from the actual payer. The payer's own WETH is only drawn for the shortfall.

ETH can accumulate in the contract through any of the `payable` entry points:

- `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` — all declared `payable` [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

- `multicall` — `payable`, used to batch calls including `refundETH` [6](#0-5) 

- `addLiquidityExactShares`, `addLiquidityWeighted` — `payable` on `MetricOmmPoolLiquidityAdder` [7](#0-6) 

The `receive()` guard correctly blocks **direct** ETH transfers from non-WETH addresses: [8](#0-7) 

However, `receive()` is **not** invoked when ETH is sent alongside a `payable` function call. There is no check that `msg.value == 0` when `tokenIn != WETH`, so ETH sent with a non-WETH swap is silently accepted and left stranded. Similarly, a user who sends excess ETH with a WETH swap and omits `refundETH()` from their multicall leaves the surplus in the contract.

---

### Impact Explanation

Once ETH is stranded in the router, any caller who executes a WETH-input swap (single-hop or multi-hop) with `amountIn ≤ address(this).balance` pays **nothing** from their own wallet: the contract wraps the stranded ETH and forwards it to the pool on their behalf. The original depositor's ETH is permanently lost to the attacker. For partial amounts (`0 < balance < amountIn`), the attacker receives a proportional subsidy. Both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` share the same `pay()` implementation, so the attack surface covers swaps and liquidity additions alike.

---

### Likelihood Explanation

The pattern of sending ETH with a non-WETH swap, or sending excess ETH and forgetting `refundETH()`, is a well-documented user error in Uniswap-style routers. The `multicall` pattern actively encourages batching ETH-value calls, making it easy to omit the refund step. No special privilege is required; any unprivileged address can trigger the exploit the moment stranded ETH exists.

---

### Recommendation

1. **Reject `msg.value` when `tokenIn != WETH`** in each swap entry point, or add a modifier that enforces `msg.value == 0` unless the first token in the path is WETH.

2. **Do not use `address(this).balance` as a payment source in `pay()`** unless the ETH was explicitly sent in the same transaction (i.e., gate the native-balance branch on `msg.value > 0` passed through as a parameter, not a raw balance read).

3. Alternatively, restrict the WETH branch of `pay()` to only consume ETH that was tracked as part of the current call (e.g., pass `msg.value` down through the call stack and use that value instead of `address(this).balance`).

---

### Proof of Concept

```
1. Alice calls exactInputSingle{value: 1 ether}(params) where params.tokenIn = USDC.
   - The swap callback calls pay(USDC, alice, pool, amount).
   - Since token != WETH, the ETH branch is skipped; USDC is pulled from Alice.
   - 1 ETH remains stranded in the router (address(router).balance == 1 ether).

2. Bob calls exactInputSingle{value: 0}(params) where params.tokenIn = WETH, params.amountIn = 1 ether.
   - The swap callback calls pay(WETH, bob, pool, 1 ether).
   - nativeBalance = 1 ether >= 1 ether → contract wraps Alice's ETH and transfers WETH to pool.
   - Bob receives the swap output without spending any WETH or ETH.
   - Alice's 1 ETH is permanently lost.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
