### Title
Missing Zero-Address Validation on `recipient` in `unwrapWETH9` Causes Permanent ETH Loss — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.unwrapWETH9` accepts an arbitrary `recipient` address with no zero-address guard. When called with `recipient = address(0)` — a realistic mistake in a multicall batch — the contract unwraps its entire WETH balance and transfers the resulting ETH to `address(0)` via a low-level call that **succeeds silently**, permanently burning the user's funds.

---

### Finding Description

`unwrapWETH9` withdraws all WETH held by the router and forwards the ETH to `recipient` through `_transferETH`:

```solidity
// PeripheryPayments.sol lines 37-45
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);   // ← no zero-address guard
    }
}
```

`_transferETH` uses a bare low-level call:

```solidity
// PeripheryPayments.sol lines 90-93
function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
}
```

A call to `address(0)` with ETH **always returns `(true, "")`** on the EVM — `address(0)` has no code, so the call is treated as a plain ETH transfer to a non-reverting EOA. The `if (!ok)` guard never fires. The ETH is burned with no revert and no event indicating loss.

There is no `if (recipient == address(0)) revert` anywhere in `unwrapWETH9` or `_transferETH`.

---

### Impact Explanation

Any ETH unwrapped from WETH and directed to `address(0)` is permanently and irrecoverably lost. The loss is bounded only by the router's WETH balance at the time of the call — which in a multicall batch can equal the user's full swap output. This is a direct loss of user principal with no recovery path.

---

### Likelihood Explanation

`unwrapWETH9` is `public payable` and is explicitly designed for composition inside `multicall` batches. In multicall usage, all parameters are ABI-encoded off-chain; a zero recipient arises from:

- A default-value encoding error (unset `address` field defaults to `address(0)` in many SDK/ABI libraries),
- An integration bug where the recipient address is computed but the computation returns zero,
- A copy-paste error in a script or frontend.

The function provides no safety net against any of these realistic mistakes.

---

### Recommendation

Add a zero-address guard at the top of `unwrapWETH9`:

```solidity
error InvalidRecipient();

function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    if (recipient == address(0)) revert InvalidRecipient();
    ...
}
```

Apply the same guard to `sweepToken` for consistency, even though standard ERC20 `safeTransfer` would revert there — the explicit check makes the contract's intent clear and protects against non-standard tokens.

---

### Proof of Concept

1. User constructs a multicall batch:
   - Call 1: `exactInputSingle(…, tokenOut=WETH, recipient=address(router))` — swaps and leaves WETH on the router.
   - Call 2: `unwrapWETH9(0, address(0))` — recipient field accidentally left as zero.

2. Call 1 executes: pool transfers WETH to the router; `balanceWETH > 0`.

3. Call 2 executes: `IWETH9(WETH).withdraw(balanceWETH)` converts WETH → ETH on the router; then `_transferETH(address(0), balanceWETH)` fires `address(0).call{value: balanceWETH}("")`.

4. The EVM executes the call to `address(0)` (no code, no revert), returns `(true, "")`.

5. `ok == true`, so `ETHTransferFailed` is never thrown. The transaction succeeds. The user's ETH is burned. [1](#0-0) [2](#0-1)

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L90-93)
```text
  function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
  }
```
