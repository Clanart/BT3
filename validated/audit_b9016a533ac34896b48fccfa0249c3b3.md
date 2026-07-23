Audit Report

## Title
Router Native ETH Balance Consumed by Unrelated WETH Swaps, Draining Stranded User Funds - (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` — the router's global native ETH balance — when settling a WETH-input swap. This balance is not scoped to the current caller or transaction. ETH stranded on the router from a prior user's `multicall` (where `refundETH()` was omitted) is silently consumed to satisfy a subsequent user's WETH payment obligation, causing direct, unprivileged theft of the stranded funds.

## Finding Description
`pay()` contains the following WETH branch:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // global, not caller-scoped
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
}
``` [1](#0-0) 

`multicall` is `payable`, so any `msg.value` sent with a multicall lands on the router and persists across transactions: [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does **not** prevent `msg.value` from accumulating on the router via `multicall` or `payable` entry points across separate transactions: [3](#0-2) 

When `exactInputSingle` or `exactInput` is called with `tokenIn = WETH`, the swap callback fires `_justPayCallback`, which calls `pay(WETH, payer, pool, value)`: [4](#0-3) 

At that point, `pay()` reads `address(this).balance`, which may include ETH left by a different user in a prior transaction, and uses it to partially or fully satisfy the current swap's WETH obligation — reducing the `transferFrom` pull from the actual payer and consuming the prior user's funds.

`refundETH()` refunds the entire router balance to `msg.sender`, but it is optional and not enforced on-chain: [5](#0-4) 

## Impact Explanation
Direct loss of user principal. User A sends `multicall{value: X}` with a WETH swap consuming only Y < X ETH and omits `refundETH()`. The remaining X−Y ETH is stranded on the router. Any subsequent caller (User B) executing a WETH-input swap of size ≥ X−Y has the stranded ETH applied toward their payment obligation, consuming User A's funds. User A loses X−Y ETH; User B pays proportionally less WETH from their own wallet. The loss is bounded only by the stranded amount, which can be arbitrarily large. This meets the Critical/High direct loss of user principal threshold.

## Likelihood Explanation
The trigger is fully unprivileged: any address calling `exactInputSingle`, `exactInput`, or `exactOutputSingle` with `tokenIn = WETH` will consume whatever native ETH is on the router. Users routinely omit `refundETH()` from multicalls — the existing test suite explicitly demonstrates the correct pattern (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth`) but the contract provides no on-chain enforcement. The attack is repeatable, requires no special permissions, and can be executed by any EOA or contract.

## Recommendation
1. **Track per-call ETH attribution**: record `address(this).balance` at the start of each top-level entry point (or at the start of `multicall`) and restrict `pay()` to use only the delta accrued during the current call, not the total balance.
2. **Alternatively**, remove implicit native-ETH-to-WETH conversion from `pay()` entirely and require callers to wrap ETH explicitly (e.g., via a dedicated `wrapETH` multicall step) before swapping, making attribution unambiguous.
3. **At minimum**, enforce `refundETH()` inclusion on-chain (e.g., revert if `address(this).balance > 0` at the end of `multicall`) to prevent ETH from being stranded across transaction boundaries.

## Proof of Concept
```solidity
// Setup: router has 0 ETH balance initially.

// Step 1 — User A strands ETH on the router:
router.multicall{value: 1000}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        pool: pool,
        tokenIn: WETH,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 500,          // only 500 of the 1000 ETH is used
        amountOutMinimum: 0,
        recipient: userA,
        deadline: block.timestamp,
        priceLimitX64: 0,
        extensionData: ""
    })))
    // NOTE: no refundETH() call — 500 ETH remains on router
]);
// router.balance == 500 ETH (User A's funds, stranded)

// Step 2 — User B calls a WETH swap with no msg.value:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: WETH,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: userB,
    deadline: block.timestamp,
    priceLimitX64: 0,
    extensionData: ""
}));
// Inside pay(WETH, userB, pool, 1000):
//   nativeBalance = 500  (User A's stranded ETH)
//   → deposits 500 ETH as WETH, transfers to pool
//   → pulls only 500 WETH from userB via transferFrom
// Result: pool receives 1000 WETH; userB pays 500 WETH instead of 1000.
// User A loses 500 ETH. User B gains a 500 WETH subsidy.
```

The corrupted value is `address(this).balance` read at line 74 of `PeripheryPayments.sol`, which is a global router state not scoped to the current caller, allowing any prior transaction's unreclaimed ETH to alter the payment split of a subsequent unrelated swap. [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
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
