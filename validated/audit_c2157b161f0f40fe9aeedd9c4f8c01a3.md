### Title
Griefing DoS on gasless transaction promotion via unauthenticated queue-slot exhaustion — (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
`IsBundleTx` treats any syntactically-matching approve/swap transaction as a "gasless bundle tx" regardless of who submitted it, and `PreAddTx` admits such transactions into a shared, capacity-limited `knownTxs` queue purely based on static pattern-matching (`IsApproveTx`/`IsSwapTx`), before the more expensive balance/allowance checks are consulted for promotion decisions. This mirrors the reported bug class: an unprivileged, cheap, repeatable action performed by an attacker imposes a shared, capacity/state restriction that blocks legitimate users' otherwise-valid transactions from being processed.

### Finding Description
`PreAddTx` classifies a transaction as a bundle tx solely via `g.IsBundleTx(tx)` → `g.IsModuleTx(tx)` → `g.IsApproveTx(tx) || g.IsSwapTx(tx)`, all of which only check the static shape of the transaction (target address is a whitelisted token/router, function selector, and for approve, that the amount is `MaxUint256`) — see [1](#0-0) . There is no check that the sender is a legitimate, distinct actor or that the tx will ever be economically executable beyond format matching, at the point it is admitted into the shared `knownTxs` queue.

`PreAddTx` immediately increments the global queue counter and rejects any further gasless-shaped tx once `MaxBundleTxsInQueue` is reached: [2](#0-1) 

Because `numQueue()` counts all `TxStatusQueue` entries across all senders in a single shared map (`knownTxs`), [3](#0-2)  an attacker who can produce valid-looking approve/swap transactions (using a whitelisted token and whitelisted swap router, which are public/known addresses per the config) at minimal cost can flood the queue up to `MaxBundleTxsInQueue` (default 200, per `DefaultGaslessConfig`) [4](#0-3) , causing every subsequent legitimate gasless transaction — including ones already signed off-chain by real users lending gas for a swap — to be rejected with `ErrBundleTxQueueFull` at `PreAddTx`.

Because entries remain "known" for `KnownTxTimeout` (30s) and queued entries persist for `QueueTimeout` (10s) before being dropped by `PreReset`, [5](#0-4)  and can be refreshed via `PreAddTx`'s "already known" branch, an attacker can continuously resubmit cheap approve/swap-shaped transactions to keep the queue saturated, similarly to how the reported iUSD bug allowed a griefer to cheaply and repeatedly re-arm a restriction (`restrictActionUntil`) ahead of every legitimate operation.

The same shared-queue design also governs pending-bundle admission via `IsReady`/`MaxBundleTxsInPending`, meaning the griefing surface extends to promotion as well: [6](#0-5) .

### Impact Explanation
This is a Medium/High-severity griefing/DoS on the gasless module: legitimate gasless users (who by definition have no native KAIA balance to pay gas and rely entirely on the gasless lending mechanism) can be indefinitely denied service by an attacker who continuously submits cheap, format-valid approve/swap-shaped transactions to saturate the fixed-size `knownTxs` queue. Because the check that gates admission (`PreAddTx`) happens before the more expensive economic checks (`GetCheckBalance`, dex amount checks) are known to matter for promotion, an attacker does not need real economic capability beyond matching the static shape checks for the tokens/router that are publicly known from `GaslessConfig.AllowedTokens`. This can indefinitely block honest users' gasless swaps/approves from ever being queued/promoted, a denial of a core public-facing mechanism (KIP-247 gasless transactions), directly analogous to the reported vector where a cheap, repeatable, unprivileged action denied users key operations.

### Likelihood Explanation
Likelihood is High: the attacker only needs knowledge of the whitelisted token and swap router addresses (public governance-configured values) and the ability to submit ordinary transactions with the correct call signature and amount pattern; PreAddTx's admission gate does not authenticate the caller's genuine intent to complete a swap, only the tx's shape. The cost per attack transaction is a single crafted call (potentially reusable/resubmitted as `knownTxs` entries expire), well within reach of any public-RPC transaction sender.

### Recommendation
- Do not admit a tx into the shared `knownTxs` queue based purely on static shape matching; require at minimum the cheap parts of `checkBalanceForApprove`/`checkBalanceForSwap` (sender code check, minimal balance check) to pass before counting toward `MaxBundleTxsInQueue`.
- Consider per-sender queue slot limits (in addition to/instead of a single global counter) so that a single account cannot exhaust the shared capacity for all other users.
- Consider rate-limiting or requiring a minimum stake/bond, or economic cost, for repeatedly occupying gasless queue slots, analogous to increasing `minMintAmount` in the original report's recommendation.

### Proof of Concept
1. Attacker reads `GaslessConfig.AllowedTokens` and the whitelisted `swapRouter` address (public config/state).
2. Attacker crafts and repeatedly submits well-formed `approve(spender=swapRouter, amount=MaxUint256)` transactions against a whitelisted token from many throwaway accounts, and/or `swapForGas(...)` calls to the whitelisted router with the correct selector — enough to satisfy `IsApproveTx`/`IsSwapTx` shape checks in `kaiax/gasless/impl/getter.go`.
3. Each accepted tx is added via `PreAddTx` to the shared `knownTxs` map with `TxStatusQueue`, incrementing `numQueue()`.
4. Once `numQueue() >= MaxBundleTxsInQueue` (default 200), all further gasless-shaped transactions — including genuine ones from real users who have no gas to submit anything else — are rejected with `ErrBundleTxQueueFull` at `kaiax/gasless/impl/tx_pool.go:47-49`.
5. Attacker resubmits similarly-shaped transactions before the `QueueTimeout`/`KnownTxTimeout` windows expire to sustain the DoS indefinitely.

### Citations

**File:** kaiax/gasless/impl/getter.go (L69-86)
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

**File:** kaiax/gasless/impl/tx_pool.go (L184-230)
```go
// Check promotion condition and enforce pending pool flow control.
func (g *GaslessModule) IsReady(txs map[uint64]*types.Transaction, next uint64, ready types.Transactions) bool {
	g.knownTxsMu.Lock()
	defer g.knownTxsMu.Unlock()

	tx, ok := txs[next]
	if !ok {
		return false
	}

	if !g.isReady(txs, next, ready) {
		return false
	}

	if g.IsBundleTx(tx) {
		// If prev tx is bundle tx, there's no need to check the knownTxs limit because it has been checked in the previous `IsReady()` execution.
		isPrevTxBundleTx := len(ready) != 0 && g.IsBundleTx(ready[len(ready)-1])
		if isPrevTxBundleTx {
			g.knownTxs.add(tx, TxStatusPending)
			return true
		}

		maxBundleTxsInPending := g.GetMaxBundleTxsInPending()
		if maxBundleTxsInPending != math.MaxUint64 {
			numExecutable := uint(g.knownTxs.numExecutable())

			numSeqTxs := uint(1)
			for i := next + 1; i < next+uint64(len(txs)); i++ {
				if tx, ok := txs[i]; ok && g.IsBundleTx(tx) {
					numSeqTxs++
				} else {
					break
				}
			}

			// false if there is possibility of exceeding max bundle tx num
			if numExecutable+numSeqTxs > maxBundleTxsInPending {
				logger.Trace("Not promoting a tx because of exceeding max bundle tx num", "tx", tx.Hash().String(), "numExecutable", numExecutable, "maxBundleTxsInPending", maxBundleTxsInPending)
				return false
			}
		}

		g.knownTxs.add(tx, TxStatusPending)
	}

	return true
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

**File:** kaiax/gasless/config.go (L80-88)
```go
func DefaultGaslessConfig() *GaslessConfig {
	return &GaslessConfig{
		AllowedTokens:         nil,
		Disable:               false,
		MaxBundleTxsInPending: 100,
		MaxBundleTxsInQueue:   200,
		BalanceCheckLevel:     BalanceCheckLevelAll,
	}
}
```
