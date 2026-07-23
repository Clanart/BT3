### Title
`refundETH()` hardcodes `msg.sender` as recipient, permanently trapping excess native ETH when called by contracts without a `receive()` function — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` unconditionally sends the router's entire native ETH balance to `msg.sender` with no `recipient` parameter. When a contract without a `receive()` function calls a payable swap function (e.g., `exactInputSingle`) with `msg.value` exceeding the actual WETH cost, the excess ETH is left in the router. The calling contract cannot recover it via `refundETH()` because the low-level ETH transfer reverts. Any third party can then call `refundETH()` and redirect the trapped ETH to themselves.

---

### Finding Description

**Step 1 — Excess ETH is left in the router after `pay()`.**

`pay()` in `PeripheryPayments.sol` handles WETH-leg payments by consuming the router's native ETH balance:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();   // wraps exactly `value`, not all
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) { ... }
``` [1](#0-0) 

When `nativeBalance >= value`, only `value` wei is wrapped and forwarded to the pool. The remainder (`nativeBalance − value`) stays as raw native ETH inside the router. This is the standard path for any caller who sends `msg.value` larger than the exact swap cost.

**Step 2 — The only recovery path is `refundETH()`, which hardcodes `msg.sender`.**

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no recipient parameter
    }
}
``` [2](#0-1) 

`_transferETH` uses a raw `call{value: value}("")` and reverts with `ETHTransferFailed()` if the transfer fails:

```solidity
function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
}
``` [3](#0-2) 

If `msg.sender` is a contract without a `receive()` or `fallback()` function, the call fails and `refundETH()` reverts. The ETH remains in the router indefinitely.

**Step 3 — Contrast with `unwrapWETH9` and `sweepToken`, which accept a `recipient`.**

Both sibling helpers accept an explicit `recipient` parameter, making them safe for contract callers:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override { ... }
function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override { ... }
``` [4](#0-3) 

`refundETH()` is the only payment helper that lacks this parameter, creating an asymmetry that silently traps ETH for contract callers.

**Step 4 — `refundETH()` is permissionless; any third party can drain the trapped ETH.**

Because `refundETH()` sends `address(this).balance` to whoever calls it, once the original depositor's call reverts, any observer can call `refundETH()` and receive the full trapped balance. The original depositor has no priority claim.

**Step 5 — The `multicall` escape hatch does not help.**

The router's `multicall` batches calls via `delegatecall`:

```solidity
function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
        results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
}
``` [5](#0-4) 

If the contract caller batches `exactInputSingle` + `refundETH()` in one multicall, the `refundETH()` leg still tries to send ETH to `msg.sender` (the calling contract). If that contract has no `receive()`, the multicall reverts entirely — meaning the swap itself is also rolled back. The contract caller cannot complete a WETH swap with any `msg.value` safety buffer at all.

---

### Impact Explanation

**Direct loss of user principal.** Any contract without a `receive()` function (custom aggregators, vaults, non-payable multisig modules) that calls a payable swap function with `msg.value > exact WETH cost` will have the excess ETH permanently inaccessible. A third-party frontrunner can call `refundETH()` and steal the full stranded balance. There is no admin recovery path; `sweepToken` and `unwrapWETH9` do not cover native ETH.

---

### Likelihood Explanation

**Medium.** Contracts interacting with DeFi routers (aggregators, vaults, on-chain bots) commonly lack `receive()` functions. The trigger condition — sending any `msg.value` larger than the exact WETH cost — is the normal defensive pattern for callers who cannot predict the exact swap cost at submission time. Multisig wallets (Gnosis Safe) do have `receive()`, but many other integration patterns do not.

---

### Recommendation

Add a `recipient` parameter to `refundETH()`, consistent with `unwrapWETH9` and `sweepToken`:

```solidity
function refundETH(address recipient) external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(recipient, balance);
    }
}
```

This allows contract callers to specify an EOA or a payable address as the refund destination, eliminating both the trap and the theft vector.

---

### Proof of Concept

1. `ContractA` (no `receive()`) calls `exactInputSingle` with `msg.value = 1 ETH` for a WETH-in swap that costs exactly `0.5 ETH`.
2. Inside `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, ContractA, pool, 0.5 ETH)`:
   - `nativeBalance = 1 ETH >= value = 0.5 ETH`
   - Router wraps `0.5 ETH` → WETH → pool.
   - `0.5 ETH` remains as native ETH in the router.
3. Swap completes successfully; `ContractA` receives the output token.
4. `ContractA` calls `refundETH()`:
   - `_transferETH(ContractA, 0.5 ETH)` → low-level call to `ContractA` with no `receive()` → `ok = false` → reverts with `ETHTransferFailed()`.
5. Bob (frontrunner) calls `refundETH()`:
   - `_transferETH(Bob, 0.5 ETH)` → succeeds.
   - Bob receives `0.5 ETH` that belonged to `ContractA`.
6. `ContractA` has permanently lost `0.5 ETH` with no recourse.

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L90-93)
```text
  function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
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
