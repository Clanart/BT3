### Title
Gasless bundle-tx queue can be forcibly filled by an unprivileged sender, blocking legitimate gasless (KIP-247) users bridge-wide - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `kaiax/gasless` module enforces a single, node-wide cap (`MaxBundleTxsInQueue`, default 200) on the number of `GaslessApproveTx`/`GaslessSwapTx` transactions that may sit in the tx pool's queue at once [1](#0-0) . This mirrors the externally reported bridge issue: a single global limiter/queue that any unprivileged transaction can trip, denying the resource to every other user of the feature until the limiter drains.

### Finding Description
`TxPool.add()` invokes `module.PreAddTx(tx, local)` **before** it runs `pool.validateTx(tx)` (which performs the gasless balance/format checks via `GetCheckBalance`) [2](#0-1) . `GaslessModule.PreAddTx` classifies any tx matching the approve/swap function-selector pattern as a bundle tx and immediately reserves a slot in the shared `knownTxs` map, rejecting new bundle txs once `numQueue() >= MaxBundleTxsInQueue`: [3](#0-2) 

`IsApproveTx`/`IsSwapTx` classification only requires a legacy-type tx calling `approve(spender, amount)` on a whitelisted token with `spender == swapRouter` and `amount == MaxUint256` (for approve), or `swapForGas(...)` on the router with a whitelisted token (for swap) — no real balance is required to be *classified* as a bundle tx: [4](#0-3) 

Because `numQueue()` counts entries across **all senders** globally (not per-account) [5](#0-4) , and the slot is reserved in `PreAddTx` before the balance check in `validateTx` even runs, an attacker can submit many approve/swap-shaped transactions from disposable EOAs (paying only ordinary gas) that will consume queue slots and return `ErrBundleTxQueueFull` for every subsequent legitimate gasless user for up to `QueueTimeout`/`KnownTxTimeout` (10s/30s) per wave [6](#0-5) . Repeating this before each new queue-timeout window keeps the shared queue saturated indefinitely, denying the gasless feature to all users, directly analogous to how Eve/Mallory forcibly activate RootERC20PredicateFlowRate's global withdrawal queue to block Alice's/other legitimate withdrawals.

### Impact Explanation
This is a griefing/DoS vector against a value-transfer-adjacent feature (KIP-247 gasless swaps, which move ERC-20/native value on behalf of users who cannot otherwise pay gas). A malicious, unprivileged transaction sender can persistently deny legitimate users the ability to enqueue gasless approve/swap transactions, degrading a bridge-like/onboarding UX feature network-wide, matching the "hinder bridge operation" impact class in the source report (medium severity: availability/DoS of a specific value-moving subsystem, not direct fund theft).

### Likelihood Explanation
Likelihood is high: the attacker only needs ordinary gas funds (no token balance is required to be classified/counted as a bundle tx in `PreAddTx`, since the balance check happens afterward in `validateTx`), needs to craft transactions matching a public, documented ABI pattern (KIP-247), and needs no special permissions, staking, or governance access. The default `MaxBundleTxsInQueue` of 200 is a modest number to keep saturated with automated spam.

### Recommendation
- Move or duplicate the classification/queue-slot reservation logic so that `PreAddTx` only reserves a `knownTxs` slot after a lightweight but sufficient validity check (e.g., non-zero token balance / minimal deposit), or run `GetCheckBalance` before `PreAddTx` for module transactions.
- Consider per-sender sub-limits within the shared `MaxBundleTxsInQueue` bound so no single account (or a batch of freshly generated accounts under a rate limit) can consume the entire global queue capacity.
- Add a per-IP/per-connection or PoW-style admission cost for bundle-tx submission at the RPC layer to raise the cost of generating many disposable senders.
- Re-evaluate whether `KnownTxTimeout`/`QueueTimeout` durations are short enough to bound the griefing window while still serving legitimate 2-step approve+swap flows.

### Proof of Concept
1. Attacker generates `N = MaxBundleTxsInQueue` (default 200) throwaway EOAs, each funded with only enough native token to pay tx gas (no ERC-20 balance required).
2. For each EOA, attacker submits a legacy tx to a whitelisted gasless token calling `approve(swapRouter, MaxUint256)` — this passes `IsApproveTx`'s static checks (`kaiax/gasless/impl/getter.go:74-86`) and is treated as a bundle tx.
3. `TxPool.add()` calls `GaslessModule.PreAddTx` first: `knownTxs.numQueue()` climbs to `MaxBundleTxsInQueue`, after which further calls return `ErrBundleTxQueueFull` — even though these attacker txs will later fail `validateTx`'s `checkBalanceForApprove` (insufficient sender token balance) and be discarded, they have already consumed and vacated? No — since `PreAddTx` and `validateTx` both run inside the same `add()` call before returning, but the successful bundle txs (the first ~200 that got a queue slot) stay in `knownTxs` for `KnownTxTimeout` (30s) regardless of later validation outcome for txs that filled the cap.
4. Any real gasless user's approve/swap tx submitted meanwhile hits `PreAddTx`'s `numQueue() >= MaxBundleTxsInQueue` check and is rejected with `ErrBundleTxQueueFull` (`kaiax/gasless/impl/errors.go:39`), denying their gasless transaction from entering the pool.
5. Attacker repeats every ~10–30 seconds (`QueueTimeout`/`KnownTxTimeout`) to keep the shared queue perpetually saturated.

Note: I was not able to fully verify from static reading alone whether transactions that are later rejected by `validateTx` (e.g., for insufficient balance) are actively purged from `knownTxs` synchronously within the same `add()` call, or only expire after `KnownTxTimeout`. This affects the precise attack cadence/cost but not the fundamental design flaw: `PreAddTx`'s global counter is incremented before the balance/format validation in `validateTx` runs, and the counter is shared across all senders with no per-account limit.

### Citations

**File:** kaiax/gasless/config.go (L48-54)
```go
	MaxBundleTxsInQueueFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-queue",
		Usage:    "max number of gasless bundle txs in queue. Default value is 200. No limit if negative value",
		Value:    200,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-queue"},
		Category: "KAIAX",
	}
```

**File:** blockchain/tx_pool.go (L1139-1161)
```go
func (pool *TxPool) add(tx *types.Transaction, local bool) (bool, error) {
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			err := module.PreAddTx(tx, local)
			if err != nil {
				return false, err
			}
			break
		}
	}

	// If the transaction is already known, discard it
	hash := tx.Hash()
	if pool.all.Get(hash) != nil {
		logger.Trace("Discarding already known transaction", "hash", hash)
		return false, fmt.Errorf("known transaction: %x", hash)
	}
	// If the transaction fails basic validation, discard it
	if err := pool.validateTx(tx); err != nil {
		logger.Trace("Discarding invalid transaction", "hash", hash, "err", err)
		invalidTxCounter.Inc(1)
		return false, err
	}
```

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

**File:** kaiax/gasless/impl/tx_counter.go (L114-122)
```go
func (k knownTxs) numQueue() int {
	num := 0
	for _, knownTx := range k {
		if knownTx.status == TxStatusQueue {
			num++
		}
	}
	return num
}
```
