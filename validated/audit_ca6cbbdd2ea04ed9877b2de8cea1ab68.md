### Title
Unrestricted `sweepToken`, `unwrapWETH9`, and `refundETH` Allow Any Caller to Drain Router ETH/Token Balances — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.sweepToken` and `PeripheryPayments.unwrapWETH9` are unrestricted `public payable` functions that allow **any caller** to drain the router's entire ERC20 or WETH balance to an **arbitrary `recipient`** address. `refundETH` is similarly unrestricted and sends the full native ETH balance to `msg.sender`. Both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` inherit these functions. When users send ETH for WETH-denominated swaps or liquidity additions, excess ETH accumulates on the router. An attacker can steal this ETH by calling `refundETH()`, or drain any ERC20/WETH balance by calling `sweepToken`/`unwrapWETH9` with an attacker-controlled `recipient`.

---

### Finding Description

`PeripheryPayments` exposes three unrestricted token-recovery functions with no caller validation: [1](#0-0) 

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override { ... }
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override { ... }
function refundETH() external payable override { ... }
```

None of these functions restrict the caller or validate that the caller is the rightful owner of the balance. `sweepToken` and `unwrapWETH9` additionally accept an arbitrary `recipient`, enabling an attacker to redirect the entire router balance to any address.

The router accumulates a native ETH balance whenever a user sends `msg.value` for a WETH-denominated swap. Inside `PeripheryPayments.pay`, when `token == WETH` and the router holds native ETH, it deposits that ETH as WETH and forwards it to the pool: [2](#0-1) 

If the user sends more ETH than the swap consumes, the surplus remains on the router. The user is expected to call `refundETH()` in the **same `multicall`** to recover it. If they omit that call, or issue it in a separate transaction, an attacker can front-run the recovery and steal the ETH.

Both `MetricOmmSimpleRouter` and `MetricOmmPoolLiquidityAdder` inherit `PeripheryPayments` and expose all three functions: [3](#0-2) [4](#0-3) 

The `multicall` entry point on both contracts is `public payable`, meaning a user can atomically combine a swap with `refundETH()`. However, nothing prevents a third party from calling `refundETH()`, `sweepToken`, or `unwrapWETH9` in a standalone transaction at any time the router holds a balance. [5](#0-4) 

---

### Impact Explanation

- **Direct ETH theft**: Any ETH left on the router (from excess `msg.value` in WETH swaps or liquidity additions) can be claimed by any caller via `refundETH()`.
- **Arbitrary-recipient drain**: `sweepToken(token, 0, attackerAddress)` and `unwrapWETH9(0, attackerAddress)` allow an attacker to redirect the router's entire ERC20 or WETH balance to any address, not just to themselves.
- **Scope**: Both `MetricOmmSimpleRouter` (swap router) and `MetricOmmPoolLiquidityAdder` (liquidity adder) are affected.

This is a direct loss of user principal — excess ETH sent for WETH swaps is a normal, expected usage pattern.

---

### Likelihood Explanation

**Medium.** Users routinely send ETH with WETH swaps and rely on `refundETH()` to recover the surplus. If that call is omitted from the multicall (user error) or issued in a separate transaction (front-runnable), the ETH is exposed. An attacker can passively monitor the router's ETH balance and call `refundETH()` whenever it is non-zero. No special privileges or setup are required.

---

### Recommendation

Restrict `sweepToken` and `unwrapWETH9` to send only to `msg.sender`, mirroring the behaviour of `refundETH()`:

```solidity
// Before
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override { ... }

// After
function sweepToken(address token, uint256 amountMinimum) public payable override {
    address recipient = msg.sender;
    ...
}
```

Apply the same change to `unwrapWETH9`. This eliminates the ability for an attacker to redirect the router's balance to an arbitrary address while preserving the intended recovery functionality for the legitimate caller.

---

### Proof of Concept

**Scenario A — ETH theft via `refundETH()`:**

1. User calls `exactInputSingle` with `msg.value = 1 ETH` and WETH as `tokenIn`; the swap only consumes 0.9 ETH.
2. `PeripheryPayments.pay` deposits 0.9 ETH as WETH and transfers it to the pool; 0.1 ETH remains on the router.
3. User does not include `refundETH()` in the same multicall (or submits it as a separate transaction).
4. Attacker calls `refundETH()` — the router's 0.1 ETH balance is transferred to the attacker.

**Scenario B — ERC20 drain via `sweepToken`:**

1. Any ERC20 token balance accumulates on the router (e.g., from a failed multi-hop intermediate step, or a user accidentally sending tokens directly).
2. Attacker calls `sweepToken(token, 0, attackerAddress)`.
3. The router's entire balance of `token` is transferred to `attackerAddress`. [6](#0-5) [7](#0-6)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-63)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L19-24)
```text
contract MetricOmmSimpleRouter is MetricOmmSwapRouterBase, PeripheryPayments, SelfPermit, IMetricOmmSimpleRouter {
  /// @notice Transient callback mode is not supported by this router.
  /// @param callbackMode Unrecognized mode read from transient storage.
  error InvalidCallbackMode(uint8 callbackMode);

  constructor(address weth, address factory) MetricOmmSwapRouterBase(factory) PeripheryPayments(weth) {}
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L22-37)
```text
contract MetricOmmPoolLiquidityAdder is IMetricOmmPoolLiquidityAdder, PeripheryPayments {
  // ============ Constants ============

  uint256 internal constant WAD = 1e18;

  uint8 internal constant KIND_PROBE = 0;
  uint8 internal constant KIND_PAY = 1;

  uint256 private constant T_SLOT_PAY_PAYER = 0;
  uint256 private constant T_SLOT_PAY_POOL = 1;
  uint256 private constant T_SLOT_PAY_MAX0 = 2;
  uint256 private constant T_SLOT_PAY_MAX1 = 3;

  // ============ Constructor ============

  constructor(address weth) PeripheryPayments(weth) {}
```
