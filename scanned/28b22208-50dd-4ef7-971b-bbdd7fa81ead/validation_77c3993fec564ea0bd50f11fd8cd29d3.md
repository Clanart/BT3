The code is fully readable. The analysis is complete from the source.

The vulnerability is confirmed. Here is the full finding.

---

### Title
Unprivileged caller can steal all WETH stranded on the router via `unwrapWETH9(0, attacker)` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`unwrapWETH9` is `public payable` with no access control and accepts a caller-supplied `recipient`. When called with `amountMinimum = 0`, the only guard (`balanceWETH < amountMinimum`) is trivially bypassed because a `uint256` is never less than zero. Any WETH balance held by the router is then fully unwrapped and forwarded to the attacker's chosen address.

---

### Finding Description

`PeripheryPayments.unwrapWETH9` reads the router's entire WETH balance, checks that it meets `amountMinimum`, and unconditionally sends the full amount as ETH to `recipient`: [1](#0-0) 

There is no `msg.sender` check, no ownership mapping, and no per-user accounting. The guard on line 39 is:

```
if (balanceWETH < amountMinimum) revert InsufficientWETH(...)
```

With `amountMinimum = 0` this condition is `uint256 < 0`, which is always `false` in Solidity. The guard is permanently disabled by the caller's own input. [2](#0-1) 

`sweepToken` has the identical structure and is equally exploitable for any ERC-20 residue. [3](#0-2) 

---

### Impact Explanation

Any WETH that lands on the router — regardless of which user deposited it — can be immediately claimed by an unprivileged attacker. The attacker receives the full ETH value; the legitimate user receives nothing. This is a direct, unconditional loss of user principal with no protocol-side mitigation.

---

### Likelihood Explanation

WETH reaches the router in the normal intended flow: a user routes a token→WETH swap with `recipient = address(router)` as step 1 of a multicall, then calls `unwrapWETH9` as step 2. If the user:

- submits step 1 as a standalone transaction and step 2 in a separate transaction (a reasonable pattern for wallets that batch manually), or
- directly transfers WETH to the router before calling `unwrapWETH9`,

an attacker monitoring the mempool can front-run step 2 with `unwrapWETH9(0, attacker)` and drain the full balance. The attack requires zero privileges, zero capital, and a single call.

---

### Recommendation

Restrict `unwrapWETH9` and `sweepToken` to `msg.sender` as the only permitted recipient, or enforce that `recipient == msg.sender`. Alternatively, track per-user WETH credits inside the router and only allow each user to unwrap their own credited amount. The simplest safe fix:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    ...
}
```

---

### Proof of Concept

1. Alice does `exactInputSingle(tokenIn=token1, tokenOut=WETH, recipient=address(router))` in a standalone transaction. The router now holds `N` WETH.
2. Before Alice can call `unwrapWETH9(N, alice)`, Bob calls `unwrapWETH9(0, bob)`.
3. The guard: `N < 0` → `false` → no revert.
4. `IWETH9(WETH).withdraw(N)` runs; router receives `N` ETH.
5. `_transferETH(bob, N)` runs; Bob receives `N` ETH.
6. Alice's WETH is gone. Router WETH balance is zero. [1](#0-0)

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
