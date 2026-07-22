The full contract code is now clear. Let me trace the exact execution path.

The full execution path is clear. Here is the assessment:

---

### Title
Unguarded `refundETH()` allows any caller to steal excess ETH left in `MetricOmmPoolLiquidityAdder` after a direct `addLiquidityExactShares` call — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` sends the contract's entire ETH balance to `msg.sender` with no access control. Because `addLiquidityExactShares` is `payable` and `_addLiquidity` never refunds excess ETH, any ETH left over after a direct (non-`multicall`) invocation is immediately stealable by any third party.

---

### Finding Description

`refundETH()` is implemented as:

```solidity
// PeripheryPayments.sol:58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no caller check
    }
}
``` [1](#0-0) 

There is no check that `msg.sender` is the address that originally deposited the ETH. The function is `external` and callable by anyone at any time.

ETH enters the contract legitimately via `addLiquidityExactShares{value: X}(...)` for a WETH pool. Inside `pay()`, only the exact amount the pool requests is wrapped and forwarded; any surplus stays in the contract:

```solidity
// PeripheryPayments.sol:73-84
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();          // wraps only `value`
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) { ... }
    ...
}
``` [2](#0-1) 

`_addLiquidity` performs no ETH refund after the pool call returns:

```solidity
// MetricOmmPoolLiquidityAdder.sol:194-206
try IMetricOmmPoolActions(pool)
  .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
  uint256 a0, uint256 a1
) {
    amount0Added = a0;
    amount1Added = a1;
    _clearPayContext();          // no ETH refund here
} catch ...
``` [3](#0-2) 

The `receive()` guard only blocks bare ETH transfers; it does not prevent ETH from entering via `payable` function calls:

```solidity
// PeripheryPayments.sol:32-34
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
``` [4](#0-3) 

The intended safe pattern is `multicall([addLiquidityExactShares(...), refundETH()])`, which is atomic because `multicall` uses `delegatecall` and preserves `msg.sender`. The test confirms this:

```solidity
// test:94-109
calls[0] = abi.encodeWithSelector(ADD_LIQUIDITY_EXACT_SHARES_WITH_OWNER, ...);
calls[1] = abi.encodeWithSelector(helper.refundETH.selector);
helper.multicall{value: msgValue}(calls);
``` [5](#0-4) 

But the contract provides no enforcement that `addLiquidityExactShares` must be called through `multicall`. A user who calls it directly with excess ETH leaves that ETH exposed.

---

### Impact Explanation

Any ETH remaining in the contract after a direct `addLiquidityExactShares` call is immediately claimable by any address via `refundETH()`. The user loses the full surplus. This is a direct, complete loss of the user's unspent native ETH with no recovery path.

---

### Likelihood Explanation

MEV bots routinely monitor the mempool for exactly this pattern. A user who calls `addLiquidityExactShares{value: X}(...)` directly (a natural usage given the function is `payable`) and sends more ETH than the pool needs will lose the surplus in the same block. The attack requires no special privileges, no malicious pool, and no non-standard tokens.

---

### Recommendation

Add an automatic ETH refund at the end of `_addLiquidity`:

```solidity
function _addLiquidity(...) internal returns (...) {
    _setPayContext(...);
    try ... {
        ...
        _clearPayContext();
    } catch { ... }
    // refund any unused ETH to the payer
    uint256 leftover = address(this).balance;
    if (leftover > 0) _transferETH(payer, leftover);
}
```

Alternatively, restrict `refundETH()` so it can only be called via `delegatecall` (i.e., from within `multicall`), preventing standalone invocations.

---

### Proof of Concept

1. WETH pool exists with token0 = WETH, token1 = ERC20.
2. User calls `adder.addLiquidityExactShares{value: 2 ether}(pool, salt, deltas, 2e18, 0, "")` directly (not via `multicall`). Pool only needs 1 ETH worth of WETH.
3. `pay()` wraps and forwards exactly 1 ETH; 1 ETH remains in `adder`.
4. Attacker calls `adder.refundETH()` in the same or next block.
5. Attacker receives 1 ETH. User's surplus is gone. [1](#0-0) [2](#0-1) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-206)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
```

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.native.t.sol (L94-109)
```text
  function test_multicall_ethInput_refundsUnusedEth() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 80_000);
    uint256 msgValue = 2 ether;
    uint256 aliceEthBefore = alice.balance;

    vm.prank(alice);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      ADD_LIQUIDITY_EXACT_SHARES_WITH_OWNER, address(pool), alice, uint80(23), d, 1_000 ether, 1_000 ether, ""
    );
    calls[1] = abi.encodeWithSelector(helper.refundETH.selector);
    helper.multicall{value: msgValue}(calls);

    assertGt(stateView.positionBinShares(address(pool), alice, 23, int8(4)), 0, "shares minted");
    assertLt(alice.balance, aliceEthBefore, "alice paid for liquidity");
    _assertAdderEmpty();
```
