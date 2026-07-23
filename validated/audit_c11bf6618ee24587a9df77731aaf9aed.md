Audit Report

## Title
Stranded Native ETH on the Router Is Silently Consumed by Any Subsequent WETH Swap via `pay()`, or Directly Stolen via `refundETH()` / `sweepToken()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` helper in `PeripheryPayments` uses the router's entire native ETH balance (`address(this).balance`) to satisfy WETH payment obligations without any attribution to the caller who deposited that ETH. Because all swap and liquidity entry points are `payable` and `pay()` only consumes exactly `value` ETH (leaving any excess on the contract), combined with the fully public, attribution-free `refundETH()` and `sweepToken()` helpers, any ETH stranded on the router from a prior user's transaction can be stolen outright or silently consumed to subsidize a different user's swap.

## Finding Description
In `PeripheryPayments.pay()`, the WETH branch reads `address(this).balance` globally:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer pays NOTHING
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
``` [1](#0-0) 

When `nativeBalance >= value`, the function deposits the router's own ETH as WETH and forwards it to the pool without pulling a single token from `payer`. The `payer` field (the actual swap caller) is completely bypassed.

ETH becomes stranded because `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` are all `payable`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

And `addLiquidityExactShares` in `MetricOmmPoolLiquidityAdder` is also `payable`: [6](#0-5) 

The three public helpers then allow anyone to drain whatever is left with no access control:

- `refundETH()` — sends the router's entire ETH balance to `msg.sender`: [7](#0-6) 

- `sweepToken()` — sends the router's entire balance of any ERC-20 to a caller-chosen `recipient`: [8](#0-7) 

- `unwrapWETH9()` — withdraws the router's entire WETH balance and sends ETH to a caller-chosen `recipient`: [9](#0-8) 

Note: the `receive()` function only blocks plain ETH transfers from non-WETH addresses, but does not prevent ETH from being sent via `msg.value` in calls to `payable` functions: [10](#0-9) 

None of these helpers record or verify which user deposited the balance they are draining.

## Impact Explanation
Direct loss of user principal. A user who sends excess `msg.value` (e.g., sends 1 ETH for a 0.5 ETH swap without including `refundETH` in the same multicall) permanently loses the residual ETH to the next caller. The loss is exact and immediate:

- **Theft path**: attacker calls `refundETH()` and receives the full stranded balance.
- **Free-swap path**: attacker calls any WETH-input swap; `pay()` silently uses the stranded ETH to cover the attacker's payment obligation, so the attacker pays zero from their own wallet.

Both paths result in a direct, quantifiable loss of the victim's ETH equal to the stranded amount. This meets the Critical/High direct loss of user principal threshold.

## Likelihood Explanation
Stranding ETH on the router is a realistic user error requiring no special privilege, no malicious setup, and no flash loan. Any of the following suffices: a user sends a round-number `msg.value` for a swap whose actual cost is less and omits `refundETH` from the multicall; an integrator constructs a multicall without a trailing `refundETH` step; or a user calls `exactInputSingle{value: X}` directly (not via multicall) with `amountIn < X`. The attack is repeatable and front-runnable in the same block.

## Recommendation
**Option A — Attribute ETH to the current `msg.sender` in `pay()`**: Track how much ETH the current top-level caller deposited (e.g., via a transient slot set at the start of each payable entry point) and only allow `pay()` to consume up to that attributed amount.

**Option B — Restrict `refundETH` to the original depositor**: Store the depositor's address in transient storage at the start of each payable call and enforce it in `refundETH()`.

**Option C (minimal)**: Document that callers must always include `refundETH` in the same multicall and add an assertion in `pay()` that reverts if `nativeBalance > value` (i.e., refuse to use more ETH than the exact swap cost, forcing the caller to manage their own balance explicitly).

## Proof of Concept
```
Setup:
  - Router deployed with WETH address.
  - Pool exists for WETH/Token1.
  - Victim (Alice) and Attacker (Bob).

Step 1 — Alice strands ETH:
  Alice calls exactInputSingle{value: 1 ETH}(
      tokenIn  = WETH,
      amountIn = 0.5 ETH,
      ...
  )
  // pay() deposits 0.5 ETH as WETH, sends to pool.
  // 0.5 ETH remains on the router (address(router).balance == 0.5 ETH).
  // Alice did not include refundETH in the same multicall.

Step 2a — Bob steals via refundETH (direct theft):
  Bob calls router.refundETH()
  // refundETH() reads address(this).balance == 0.5 ETH
  // transfers 0.5 ETH to Bob (msg.sender)
  // Alice loses 0.5 ETH; Bob gains 0.5 ETH at zero cost.

Step 2b — Bob steals via free swap (pay() silent consumption):
  Bob calls exactInputSingle(
      tokenIn  = WETH,
      amountIn = 0.5 ETH,
      ...
  )
  // pay() sees nativeBalance (0.5 ETH) >= value (0.5 ETH)
  // deposits router's 0.5 ETH as WETH, sends to pool
  // safeTransferFrom(Bob, ...) is NEVER called
  // Bob receives Token1 output; pays nothing from his own wallet.
  // Alice loses 0.5 ETH; Bob gains a full swap for free.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-45)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
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
