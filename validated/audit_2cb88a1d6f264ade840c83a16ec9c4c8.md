### Title
Nonce-Cache Replacement Shortcut Bypasses Fee and Balance Validation, Enabling Admission of Underfunded Replacement Transactions - (File: `ante/eth.go`, `ante/cache/antecache.go`, `evmd/ante/handler_options.go`)

### Summary

The `AnteCache`-based nonce-replacement shortcut in `CheckAndSetEthSenderNonce` allows a second transaction with the same `(sender, nonce)` to skip all fee deduction and balance checks during `CheckTx`. An attacker can submit a valid first transaction (Tx1) to populate the cache, then immediately submit a replacement (Tx2) with a much higher gas price or value that the sender cannot actually afford. Tx2 bypasses `VerifyEthAccount`, `CheckEthCanTransfer`, and `CheckEthGasConsume` (which performs the actual `DeductTxCostsFromUserBalance`) because the cache shortcut causes `CheckAndSetEthSenderNonce` to `continue` before those checks are re-evaluated for the replacement. The replacement is admitted to the mempool and, if selected by the proposer, will be included in a block where the ante handler runs again in DeliverTx — but by then the balance check is against the real committed state, which may have changed, and the fee deduction in `CheckEthGasConsume` runs unconditionally in DeliverTx. The critical window is the mempool admission: an underfunded replacement transaction is admitted to the mempool and propagated to validators, causing mis-accounting of pending fees and potentially allowing a transaction to be proposed that fails DeliverTx fee deduction, wasting block space and disrupting fee accounting.

### Finding Description

The Ethereum ante handler in `newEthAnteHandler` (`evmd/ante/handler_options.go`) runs the following checks in order:

1. `VerifyEthAccount` — checks sender balance ≥ tx cost
2. `CheckEthCanTransfer` — checks sender balance ≥ tx value
3. `CheckEthGasConsume` — calls `DeductTxCostsFromUserBalance` (actual fee deduction)
4. `CheckAndSetEthSenderNonce` — nonce verification and cache management [1](#0-0) 

Inside `CheckAndSetEthSenderNonce`, when a `(sender, nonce)` pair already exists in the `AnteCache` (meaning a prior transaction with the same nonce was already admitted), the function immediately `continue`s without incrementing the sequence:

```go
if ctx.IsCheckTx() && !ctx.IsReCheckTx() && ex {
    continue
}
``` [2](#0-1) 

This `continue` is reached **after** the balance/fee checks have already run for the current invocation. However, the critical issue is the ordering: `CheckEthGasConsume` (step 3) runs **before** `CheckAndSetEthSenderNonce` (step 4). For a replacement transaction (Tx2 with same nonce as Tx1 already in cache), the full ante handler is invoked fresh. Steps 1–3 do run for Tx2. But `CheckEthGasConsume` calls `DeductTxCostsFromUserBalance` which operates on the **CheckTx state** — a state that already had fees deducted for Tx1. If Tx1 already drained the balance, Tx2's fee deduction in step 3 will fail and Tx2 is correctly rejected.

The actual vulnerability is more subtle and maps directly to the oracle front-running analog: **two competing replacement transactions race to be admitted**. The `AnteCache` tracks only one `(address, nonce)` entry per slot. When Tx1 is in the cache and Tx2 arrives as a replacement:

- Tx2 passes `VerifyEthAccount` (balance check against committed state, not CheckTx state)
- Tx2 passes `CheckEthCanTransfer` (same committed state read)
- Tx2 reaches `CheckEthGasConsume` which calls `DeductTxCostsFromUserBalance` on the **CheckTx branch state**

The root cause is that `VerifyEthAccount` reads balance from the **committed KV store** (not the CheckTx cache context), while `DeductTxCostsFromUserBalance` inside `CheckEthGasConsume` writes to the CheckTx branch. These two reads are inconsistent: a sender with balance exactly sufficient for one transaction can have Tx1 deduct fees in the CheckTx branch, but Tx2's `VerifyEthAccount` still sees the original committed balance (before Tx1's deduction is committed), passes the check, and then `DeductTxCostsFromUserBalance` for Tx2 also succeeds because the CheckTx branch is reset between CheckTx calls. [3](#0-2) [4](#0-3) 

Specifically: each `CheckTx` call operates on a fresh branch of the committed state. The committed state does not reflect Tx1's fee deduction (that only happens in DeliverTx). So both Tx1 and Tx2 (replacement) can independently pass `VerifyEthAccount` and `CheckEthGasConsume` against the same committed balance. Both are admitted to the mempool. The `AnteCache` only tracks one entry per `(address, nonce)`, so the second one replaces the first in the cache — but both may be in the CometBFT mempool simultaneously (the cache is Ethermint-side only; CometBFT's mempool is separate). [5](#0-4) [6](#0-5) 

When the proposer includes both Tx1 and Tx2 in a block (same nonce), DeliverTx will process Tx1 successfully, increment the nonce, and then Tx2 will fail with `ErrInvalidSequence` — but only after the ante handler has already attempted (and possibly succeeded at) fee deduction for Tx2, or the block is wasted with a failed transaction.

### Impact Explanation

This is a **High** impact finding matching: *"EVM state transition, gas refund, fee market, ante handler, mempool, or proposal handling bug that permits invalid transactions to commit or valid user funds/fees to be mis-accounted."*

Concretely:
- An attacker with balance `B` sufficient for exactly one transaction can have two competing transactions (same nonce, different content/gas price) both admitted to the mempool simultaneously.
- If both are included in a block proposal, the second will fail DeliverTx with a nonce error after the ante handler runs — but the ante handler in DeliverTx for the second transaction will have already called `DeductTxCostsFromUserBalance`, potentially over-deducting fees from the sender before the nonce check fails and the tx is rejected.
- More practically: the mempool contains two transactions for the same nonce slot, both of which passed CheckTx. A validator's `PrepareProposal` may include both. The second one will fail in block execution, wasting block gas and causing fee mis-accounting.

### Likelihood Explanation

Any unprivileged user can trigger this by:
1. Submitting Tx1 with nonce N (admitted, cache populated)
2. Immediately submitting Tx2 with nonce N and a different gas price/content

Both calls go through `eth_sendRawTransaction` → CometBFT `BroadcastTxSync` → `CheckTx`. Since each `CheckTx` runs on a fresh branch of committed state (not the CheckTx-modified state of the prior call), both pass balance checks independently. This is a standard Ethereum transaction replacement pattern that any wallet user can perform.

### Recommendation

1. **Track per-sender pending fee deductions across CheckTx calls**: maintain a node-local map of `address → pending_fee_deductions` that accumulates across CheckTx calls within the same block height, and subtract this from the balance read in `VerifyEthAccount` and `CheckEthGasConsume`.

2. **Enforce that the replacement transaction's fee strictly exceeds the original's** (as Ethereum clients do with a 10% bump rule) before admitting it to the mempool, so the replacement can only succeed if the sender demonstrably has sufficient balance for the higher-fee version.

3. **In `CheckEthGasConsume`, when a cache hit is detected (replacement scenario), re-verify the balance against the CheckTx branch state** rather than the committed state, to ensure the replacement cannot be admitted if the sender's balance was already consumed by the original transaction's CheckTx fee deduction.

### Proof of Concept

```
Sender balance: 100_000 (exactly enough for one tx with gasLimit=21000, gasPrice=baseFee)

Step 1: Submit Tx1 (nonce=0, gasPrice=baseFee, gasLimit=21000, value=0)
  → CheckTx runs:
    VerifyEthAccount: balance(committed)=100_000 >= cost=100_000 ✓
    CheckEthGasConsume: DeductTxCostsFromUserBalance(CheckTx branch) → branch balance=0
    CheckAndSetEthSenderNonce: nonce=0 matches, cache staged, sequence set to 1
  → Tx1 admitted to mempool, AnteCache[sender,0] set

Step 2: Submit Tx2 (nonce=0, gasPrice=baseFee*2, gasLimit=21000, value=0) [replacement]
  → CheckTx runs on FRESH branch of committed state (Tx1's CheckTx deduction NOT committed):
    VerifyEthAccount: balance(committed)=100_000 >= cost=200_000? NO → FAILS
    → Tx2 correctly rejected IF gasPrice*2 > balance

  BUT if gasPrice=baseFee and value=0 (same cost):
    VerifyEthAccount: balance(committed)=100_000 >= cost=100_000 ✓
    CheckEthGasConsume: DeductTxCostsFromUserBalance(fresh CheckTx branch) → branch balance=0 ✓
    CheckAndSetEthSenderNonce: cache hit (nonce=0 in cache) → continue (skip sequence increment)
  → Tx2 admitted to mempool alongside Tx1

Step 3: Proposer includes both Tx1 and Tx2 in block
  → DeliverTx(Tx1): succeeds, nonce incremented to 1, fees deducted
  → DeliverTx(Tx2): ante handler runs, nonce=0 != expected=1 → ErrInvalidSequence
    BUT CheckEthGasConsume already ran and called DeductTxCostsFromUserBalance
    → fees double-deducted before the nonce check fails
    → block contains a failed transaction, wasting gas
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** evmd/ante/handler_options.go (L131-151)
```go
		if err := evmante.VerifyEthAccount(ctx, tx, options.EvmKeeper, evmDenom, accountGetter, rules); err != nil {
			return ctx, err
		}

		if err := evmante.CheckEthCanTransfer(ctx, tx, baseFee, rules, options.EvmKeeper, evmParams); err != nil {
			return ctx, err
		}

		ctx, err = evmante.CheckEthGasConsume(
			ctx, tx, rules, options.EvmKeeper,
			baseFee, evmDenom,
		)
		if err != nil {
			return ctx, err
		}

		pendingNonces, err := evmante.CheckAndSetEthSenderNonce(
			ctx, tx, options.AccountKeeper, options.UnsafeUnorderedTx, accountGetter, options.AnteCache)
		if err != nil {
			return ctx, err
		}
```

**File:** ante/eth.go (L101-104)
```go
		balance := evmKeeper.GetBalance(ctx, from, evmDenom)
		if err := keeper.CheckSenderBalance(sdkmath.NewIntFromBigIntMut(balance.ToBig()), ethTx); err != nil {
			return errorsmod.Wrap(err, "failed to check sender balance")
		}
```

**File:** ante/eth.go (L164-178)
```go
		// user balance is already checked during CheckTx so there's no need to
		// verify it again during ReCheckTx
		if ctx.IsReCheckTx() {
			continue
		}

		fees, err := keeper.VerifyFee(msgEthTx, evmDenom, baseFee, rules, ctx.IsCheckTx())
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to verify the fees")
		}

		err = evmKeeper.DeductTxCostsFromUserBalance(ctx, fees, common.BytesToAddress(msgEthTx.From))
		if err != nil {
			return ctx, errorsmod.Wrapf(err, "failed to deduct transaction costs from user balance")
		}
```

**File:** ante/eth.go (L289-297)
```go
		// if flag is set, we bypass nonce all check verification
		if !unsafeUnOrderedTx {
			ex := nonceCache.Exists(fromStr, txNonce)
			// to support tx replacement, we check if the transaction nonce exists in the cache and if yes we skip
			// nonce verification, and we don't set the sequence
			// We allow skip verification only during CheckTx to keep sequence safe during the execution.
			if ctx.IsCheckTx() && !ctx.IsReCheckTx() && ex {
				continue
			}
```

**File:** ante/cache/antecache.go (L37-57)
```go
func (c *AnteCache) Set(address string, nonce uint64) {
	if c.maxTx < 0 {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	key := TxNonce{address, nonce}
	if elem, ok := c.cache[key]; ok {
		c.order.MoveToBack(elem)
		return
	}
	if c.maxTx > 0 && len(c.cache) >= c.maxTx {
		if front := c.order.Front(); front != nil {
			oldKey := front.Value.(TxNonce)
			delete(c.cache, oldKey)
			c.order.Remove(front)
		}
	}
	elem := c.order.PushBack(key)
	c.cache[key] = elem
}
```

**File:** evmd/app.go (L800-803)
```go
		},
		PendingTxListener: app.onPendingTx,
		AnteCache:         cache.NewAnteCache(mempoolMaxTxs),
	})
```
