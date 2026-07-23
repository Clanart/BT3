### Title
Unguarded `refundETH` and `sweepToken` Allow Any Caller to Steal Stranded ETH/ERC20 from the Router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.refundETH()` and `sweepToken()` carry no access control. `refundETH()` sends the router's entire ETH balance to `msg.sender`; `sweepToken()` sends the router's entire ERC20 balance to a caller-supplied `recipient`. Because the `pay()` helper can leave excess ETH on the router when a user over-funds a WETH swap, any third party can drain that ETH in a subsequent call.

---

### Finding Description

**How ETH becomes stranded**

`pay()` handles the case where the caller sends native ETH to pay for a WETH-denominated swap: [1](#0-0) 

When `nativeBalance >= value`, exactly `value` wei is wrapped and forwarded to the pool. The surplus `nativeBalance - value` remains on the router as raw ETH after the swap function returns. The user is expected to reclaim it by bundling a `refundETH()` call in the same `multicall`. If they do not, the ETH sits on the contract indefinitely.

**The unguarded drains**

`refundETH()` sends the full ETH balance to whoever calls it: [2](#0-1) 

`sweepToken()` sends the full ERC20 balance to a caller-chosen address: [3](#0-2) 

Neither function checks `msg.sender`, records who deposited funds, or restricts `recipient`. Any EOA or contract can call either function at any time.

**`multicall` does not mitigate this**

`multicall` uses `delegatecall`, so `msg.sender` is the original caller within each sub-call: [4](#0-3) 

This means a user who correctly bundles `[exactInputSingle, refundETH]` is safe. A user who omits `refundETH` from the bundle — or calls `exactInputSingle` directly — leaves ETH exposed.

---

### Impact Explanation

An attacker monitors the mempool (or simply polls the router's ETH balance). After a victim's swap leaves surplus ETH on the router, the attacker calls `refundETH()` and receives the full balance. For `sweepToken`, the attacker supplies their own address as `recipient` and drains any ERC20 balance. The victim loses the stranded amount with no recourse.

Severity is **Medium** (not High): the loss is bounded to the surplus ETH/tokens the victim failed to reclaim, not the full swap principal. It also requires the victim to omit the `refundETH` call from their multicall bundle, which is a user-side precondition.

---

### Likelihood Explanation

- Users interacting directly with `exactInputSingle` or `exactInput` (not via a frontend that auto-bundles `refundETH`) will routinely leave ETH on the router.
- The attack requires no special permissions, no malicious pool, and no oracle manipulation — just a public call.
- Front-running or block-watching is not even necessary; the attacker can poll the router balance and call at any time.

---

### Recommendation

1. **Track the original sender's ETH contribution** in transient storage at swap entry and restrict `refundETH` to return only to that address (or enforce it is called only within the same `multicall` context).
2. **Auto-refund excess ETH** at the end of each top-level swap function rather than relying on the caller to bundle a separate `refundETH` call.
3. **Restrict `sweepToken` recipient** to `msg.sender`, or add an `onlyOwner`/`onlySelf` guard so it can only be called from within a `multicall` bundle initiated by the token owner.

---

### Proof of Concept

```solidity
// Victim sends 1 ETH but swap only needs 0.9 ETH.
// pay() wraps 0.9 ETH → WETH → pool; 0.1 ETH stays on router.
router.exactInputSingle{value: 1 ether}(params); // victim tx

// Attacker (separate tx, no special role):
router.refundETH(); // attacker receives 0.1 ETH
// OR
router.sweepToken(address(weth), 0, attacker); // drains any WETH balance
```

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers to the router — it does not prevent `msg.value` ETH from accumulating during payable function calls, so the surplus ETH path is always reachable. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
