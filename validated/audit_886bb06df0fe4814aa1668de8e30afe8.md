### Title
Gasless bundle-tx admission queue can be exhausted by spam, denying service to legitimate gasless users - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
`GaslessModule.PreAddTx()` enforces a single, chain-wide cap (`GetMaxBundleTxsInQueue()`) on the number of gasless "bundle" transactions (GaslessApproveTx/GaslessSwapTx) that may sit in the tx pool queue at any time. Because transaction admission into this shared counter is triggered merely by matching a public, stateless shape check (`IsApproveTx`/`IsSwapTx`), any unprivileged tx sender can submit throwaway look-alike transactions to fill the queue and cause `ErrBundleTxQueueFull` rejections for genuine gasless users, exactly mirroring the reported `submitStrategy()` DOS pattern (a single/limited "pending slot" resource, publicly writable, that legitimate submitters must wait out via a timeout).

### Finding Description
`PreAddTx` is called for every transaction admitted to the pool: [1](#0-0) 

The check `IsBundleTx` classifies a transaction purely by shape: `IsApproveTx`/`IsSwapTx` only require the transaction to point at a whitelisted token/router with the right selector and `amount == MaxUint256`/`swapForGas` arguments — no balance, allowance, or nonce validity is checked at this stage: [2](#0-1) 

Once classified as a bundle tx, it consumes a slot in the single shared `g.knownTxs` structure bounded by `GetMaxBundleTxsInQueue()`: [3](#0-2) 

This is analogous to `Governance.submitStrategy()`'s bug: a shared, limited "pending" resource (there: one pending strategy per vault; here: a fixed-size global bundle-tx queue) that is (a) writable by any unprivileged party via a public transaction, (b) not gated by any meaningful cost/ownership check beyond matching the trigger pattern, and (c) only recovers via a timeout (`QueueTimeout = 10s`) rather than being rejected outright or made per-sender: [4](#0-3) [5](#0-4) 

An attacker with many disposable accounts (each just needs to sign a legacy `approve`/`swapForGas`-shaped call to a real whitelisted token/router; the corresponding balance/allowance checks in `GetCheckBalance()` are separate from `IsBundleTx` classification and do not gate this queue-slot consumption) can continuously submit fresh look-alike transactions to keep the shared queue saturated, so that `PreAddTx` returns `ErrBundleTxQueueFull` for legitimate gasless approve/swap submissions from other users.

### Impact Explanation
Legitimate users relying on the gasless flow (KIP-247) to swap tokens for gas can be denied service: their approve/swap transactions are rejected at admission (`ErrBundleTxQueueFull`) while the attacker refreshes spam entries roughly every `QueueTimeout` (10s). This is a availability/DoS impact on a public-facing feature (gasless onboarding), not a fund-theft bug, matching "Medium" severity as in the analog report.

### Likelihood Explanation
Likelihood is Medium: the attacker needs only to know the whitelisted token and swap-router addresses (public state, read via the module's on-chain registry) and sign cheap legacy-format transactions from disposable accounts; no special privilege, balance, or approval is required to be classified as a bundle tx and consume a queue slot, similar to the original report's spam/frontrun vector against `submitStrategy()`.

### Recommendation
- Make the bundle-tx queue cap per-sender (or per-sender bounded) rather than a single global counter, so no single actor/set of throwaway accounts can exhaust capacity for all other users.
- Require the balance/allowance pre-check (`checkBalanceForApprove`/`checkBalanceForSwap`) to pass before a transaction is allowed to consume a bundle-tx queue slot in `PreAddTx`, rather than only shape-matching.
- Consider rate-limiting bundle-tx admission per sender/IP in addition to the pool-wide cap.

### Proof of Concept
1. Query the current whitelisted `allowedTokens` and `swapRouter` addresses that `GaslessModule` tracks (public on-chain state read by the module, e.g. via `debug_isGaslessTx` or the `GaslessSwapRouter`/registry contracts).
2. From N disposable accounts (only requiring minimal balance to be included in the pool at all), craft legacy transactions:
   - `approve(spender=swapRouter, amount=MaxUint256)` to a whitelisted token (satisfies `IsApproveTx`), or
   - `swapForGas(token, amountIn, minAmountOut, amountRepay, deadline)` to the whitelisted router (satisfies `IsSwapTx`).
3. Submit `GetMaxBundleTxsInQueue()` such transactions in rapid succession; `PreAddTx` will admit them into `g.knownTxs` as `TxStatusQueue` until the cap is reached.
4. Any subsequent legitimate gasless approve/swap transaction submitted by another user is rejected with `ErrBundleTxQueueFull` (per `kaiax/gasless/impl/tx_pool.go:46-49`) until the attacker's entries expire after `QueueTimeout` (10s), at which point the attacker resubmits fresh look-alike transactions to keep the queue saturated indefinitely.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L32-36)
```go
var (
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
)
```

**File:** kaiax/gasless/impl/tx_pool.go (L38-53)
```go
func (g *GaslessModule) PreAddTx(tx *types.Transaction, local bool) error {
	g.knownTxsMu.RLock()
	defer g.knownTxsMu.RUnlock()

	if knownTx, ok := g.knownTxs.get(tx.Hash()); ok && knownTx.elapsedPromotedOrAddedTime() < KnownTxTimeout {
		return ErrUnableToAddKnownBundleTx
	}

	if g.IsBundleTx(tx) {
		if uint(g.knownTxs.numQueue()) >= g.GetMaxBundleTxsInQueue() {
			return ErrBundleTxQueueFull
		}
		g.knownTxs.add(tx, TxStatusQueue)
	}
	return nil
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L292-315)
```go
// PreReset removes timed out tx from the tx pool and knownTxs.
func (g *GaslessModule) PreReset(oldHead, newHead *types.Header) []common.Hash {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	drops := make([]common.Hash, 0)

	for hash, knownTx := range *g.knownTxs {
		// remove pending timed out tx from tx pool
		if knownTx.status == TxStatusPending && knownTx.elapsedPromotedTime() >= PendingTimeout {
			drops = append(drops, hash)
		}
		// remove queue timed out tx from tx pool
		if knownTx.status == TxStatusQueue && knownTx.elapsedAddedTime() >= QueueTimeout {
			drops = append(drops, hash)
		}
		// remove known timed out tx from knownTxs
		if knownTx.elapsedPromotedOrAddedTime() >= KnownTxTimeout {
			g.knownTxs.delete(hash)
		}
	}

	return drops
}
```

**File:** kaiax/gasless/impl/getter.go (L69-103)
```go
// IsApproveTx checks following conditions:
// A1. tx.to is a whitelisted ERC20 token.
// A2. tx.data is `approve(spender, amount)`.
// A3. spender is a whitelisted SwapRouter contract.
// A4. amount is MaxUint.
func (g *GaslessModule) IsApproveTx(tx *types.Transaction) bool {
	args, ok := decodeApproveTx(tx, g.signer)
	return ok && g.isApproveTx(args)
}

func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}

// IsSwapTx checks following conditions:
// S1. tx.to is a whitelisted SwapRouter contract.
// S2. tx.data is `swapForGas(token, amountIn, minAmountOut, amountRepay)`.
// S3. token is a whitelisted ERC20 token.
func (g *GaslessModule) IsSwapTx(tx *types.Transaction) bool {
	args, ok := decodeSwapTx(tx, g.signer)
	return ok && g.isSwapTx(args)
}

func (g *GaslessModule) isSwapTx(args *SwapArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.swapRouter == args.Router && // S1
		g.allowedTokens[args.Token] // S3
}
```
