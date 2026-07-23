The vulnerability is real. Let me trace the exact path before writing the finding.

**Root cause:** `pay()` in `PeripheryPayments.sol` reads `address(this).balance` — the contract's **total** native ETH — not just the current caller's ETH. Any ETH stranded from a prior transaction is consumed first for the next WETH-pool caller.

**How ETH gets stranded:** All liquidity entry points are `payable`. A user who calls `addLiquidityExactShares{value: X}` directly (not via `multicall` + `refundETH`) and sends more ETH than the pool needs leaves the excess on the contract with no automatic refund.

---

### Title
Stranded ETH on `MetricOmmPoolLiquidityAdder` Is Consumed as WETH Payment for Subsequent Callers, Causing Direct LP Principal Loss — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses `address(this).balance` as the source of native ETH when paying a WETH pool leg. This balance is contract-global, not per-caller. ETH left on the adder from any prior payable call is silently consumed for the next user's WETH liquidity payment, transferring the prior user's principal to the subsequent user's LP position.

### Finding Description

`pay()` in `PeripheryPayments.sol` implements a WETH hybrid path:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← total contract ETH, not caller's
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

All four liquidity entry points (`addLiquidityExactShares` ×2, `addLiquidityWeighted` ×2) are `payable`, so a caller can send ETH directly without a multicall wrapper. When the pool only needs `Y < X` ETH, `pay()` wraps exactly `Y` and the remaining `X - Y` stays on the contract: [2](#0-1) 

`refundETH()` is the only recovery path, but it is not called automatically — it must be explicitly included in a `multicall`: [3](#0-2) 

The NatSpec documents the multicall+refundETH pattern as the intended usage, but the functions are individually `payable`, making direct calls with excess ETH a natural and common mistake. There is no per-user ETH accounting and no guard in `pay()` to restrict consumption to the current caller's ETH.

### Impact Explanation
**HIGH — direct loss of user principal.** When Alice's stranded ETH is consumed for Bob's liquidity add, Alice permanently loses that ETH (it is wrapped and transferred to the pool as Bob's LP deposit). Bob receives a full or partial WETH subsidy at Alice's expense. The loss is bounded only by how much ETH Alice left stranded; in the worst case it equals the full `msg.value` she sent.

### Likelihood Explanation
**MEDIUM.** The documented pattern (multicall + refundETH) is safe, but:
- All entry points are individually `payable`, inviting direct calls with ETH.
- Sending slightly more ETH than needed (to avoid slippage reverts) is a standard user pattern from Uniswap v2/v3 muscle memory.
- Any subsequent WETH-pool caller — including a bot watching the mempool — can drain the stranded balance in the very next block.
- No privileged access is required.

### Recommendation
Two complementary fixes:

1. **Track per-call ETH:** Record `msg.value` at entry and pass it as a cap into `pay()`, so only the current call's ETH is eligible for wrapping.
2. **Auto-refund excess ETH:** At the end of each non-multicall liquidity entry point, refund `address(this).balance` to `msg.sender` unconditionally (mirroring how Uniswap v3's `exactInput` handles ETH).

### Proof of Concept

```
Setup: WETH/token1 pool. Alice has 1 ETH. Bob has WETH approved.

1. Alice calls:
   adder.addLiquidityExactShares{value: 1 ether}(pool, alice, 1, delta, 1 ether, 0, "")
   Pool needs 0.6 ETH → pay() wraps 0.6 ETH, sends to pool.
   Remaining 0.4 ETH stays on adder (no refundETH called).

2. Bob calls (no ETH sent):
   adder.addLiquidityExactShares(pool, bob, 2, delta, 0.4 ether, 0, "")
   Pool needs 0.4 ETH → pay() sees nativeBalance = 0.4 ETH ≥ value,
   wraps Alice's 0.4 ETH, sends to pool as Bob's deposit.

Result:
- Alice lost 0.4 ETH (stranded ETH consumed for Bob's LP position).
- Bob paid 0 WETH from his own balance for 0.4 ETH worth of liquidity.
- adder.balance == 0 after Bob's call.
```

The `payer` field in the transient context correctly records Bob as payer, but `pay()` never checks whether `address(this).balance` was contributed by the current `payer` — it consumes all available contract ETH unconditionally. [4](#0-3)

### Citations

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
