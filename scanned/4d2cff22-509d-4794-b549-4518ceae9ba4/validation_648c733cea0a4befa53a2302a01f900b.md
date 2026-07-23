### Title
`unwrapWETH9` Missing Zero-Address Check on `recipient` Allows Permanent ETH Burn - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.unwrapWETH9` accepts a caller-supplied `recipient` with no zero-address guard. `_transferETH(address(0), value)` executes `address(0).call{value: value}("")`, which succeeds in the EVM (address(0) has no code; the call returns `true`), permanently burning the unwrapped ETH. This is the direct analog of the LootBox `transferEther(address(0), balance)` burn.

---

### Finding Description

`unwrapWETH9` is a `public payable` function that:
1. Reads the router's full WETH balance.
2. Calls `IWETH9(WETH).withdraw(balanceWETH)` — converting all WETH to native ETH held by the router.
3. Calls `_transferETH(recipient, balanceWETH)`.

`_transferETH` is:

```solidity
function _transferETH(address to, uint256 value) internal {
    (bool ok,) = to.call{value: value}("");
    if (!ok) revert ETHTransferFailed();
}
```

When `to == address(0)`, the low-level call succeeds (EVM sends ETH to the zero address, which has no code; the call returns `true`). The `ok` check passes, no revert is triggered, and the ETH is permanently destroyed.

There is no `require(recipient != address(0))` guard anywhere in `unwrapWETH9` or `_transferETH`.

By contrast, `sweepToken` with `recipient == address(0)` would revert because OpenZeppelin's `safeTransfer` internally checks `to != address(0)` — exactly mirroring the LootBox asymmetry where `withdrawERC20/ERC721/ERC1155` reverted but `transferEther` succeeded.

---

### Impact Explanation

Any WETH balance held by the router can be permanently burned by any unprivileged caller invoking `unwrapWETH9(0, address(0))`. WETH accumulates on the router in the standard ETH-output pattern: a user swaps `tokenX → WETH` with `recipient = address(router)`, then calls `unwrapWETH9` to receive native ETH. If these two steps are not atomic (i.e., not wrapped in a single `multicall`), an attacker can front-run the second step and burn the WETH instead of letting the user claim it. The user loses their full swap output with no recourse.

---

### Likelihood Explanation

The intended usage pattern (multicall) is atomic and not vulnerable. However:
- Users who call `exactInputSingle` / `exactInput` with `recipient = address(router)` in a standalone transaction (a documented and tested pattern) create a front-running window.
- Any WETH sent directly to the router (e.g., airdrop, direct transfer) is permanently at risk.
- The attack requires no special privilege — any EOA can call `unwrapWETH9(0, address(0))`.

---

### Recommendation

Add a zero-address guard at the top of `unwrapWETH9` and `sweepToken`:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    if (recipient == address(0)) revert InvalidRecipient();
    ...
}

function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    if (recipient == address(0)) revert InvalidRecipient();
    ...
}
```

Optionally add the same guard to `_transferETH` as a defense-in-depth measure.

---

### Proof of Concept

```
1. Alice calls exactInputSingle{value: 1 ether}(tokenIn=WETH, tokenOut=token1, ..., recipient=address(router))
   → Pool sends WETH output back to router. Router now holds X WETH.

2. Bob (attacker) observes the pending unwrapWETH9(X, alice) in the mempool.
   Bob front-runs with: router.unwrapWETH9(0, address(0))

3. Execution of Bob's tx:
   - balanceWETH = X  (> 0, passes amountMinimum=0 check)
   - IWETH9(WETH).withdraw(X)  → router receives X ETH
   - _transferETH(address(0), X)
     → address(0).call{value: X}("") returns (true, "")
     → ok == true, no revert
   → X ETH permanently burned at address(0)

4. Alice's unwrapWETH9 call now sees balanceWETH == 0, receives nothing.
   Alice has lost her entire swap output.
```

**Relevant code:** [1](#0-0) [2](#0-1)

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
