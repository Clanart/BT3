### Title
Gasless bundle-tx queue can be DOSed by fee-less spam since no cost is collected for entering `MaxBundleTxsInQueue` slots - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
The `kaiax/gasless` module admits `GaslessApproveTx`/`GaslessSwapTx` transactions into a module-tracked "queue" slot count (`knownTxs.numQueue()`) bounded by `MaxBundleTxsInQueue` (default 200). Unlike ordinary transactions, gasless transactions bypass the standard sender-balance/fee check in the tx pool, because the gas is meant to be lent by the block proposer at inclusion time and repaid during the swap. This mirrors the M-8 report's root cause: a hard admission cap (`maxPendingOrders`/`MaxBundleTxsInQueue`) with no compulsory fee charged for occupying a slot, so a low-cost/no-cost actor can keep the cap saturated and lock out legitimate users.

### Finding Description
`PreAddTx` gates entry into the gasless "queue" bucket purely by a counter check, with no monetary cost enforced on the submitter at admission time: [1](#0-0) 

The balance/fee check that normally protects the tx pool from spam is explicitly skipped for gasless transactions and replaced by `GetCheckBalance`, which — depending on `BalanceCheckLevel` — can be reduced to only static amount/deadline relations, without requiring the sender to hold any KAIA or, at the lowest levels, without verifying real token balance/allowance: [2](#0-1) [3](#0-2) 

The README confirms the intent: "gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap," and that "Sender balance check is omitted for gasless transactions": [4](#0-3) 

The queue cap itself is small and static (default 200) and configurable down to as low as an operator chooses: [5](#0-4) 

An attacker who generates fresh, unfunded (or minimally funded) addresses can construct valid-looking `GaslessApproveTx`/`GaslessSwapTx` payloads (satisfying `IsApproveTx`/`IsSwapTx` pattern matching) targeting a whitelisted token/router, sign them for free, and submit them repeatedly. Each occupies one of the `MaxBundleTxsInQueue` slots via `PreAddTx`. Once these entries time out after `QueueTimeout` (10s) and are dropped in `PreReset`, the attacker can immediately resubmit new ones from new addresses at effectively zero cost (no balance drained, since the balance check is bypassed and gas is not paid by the attacker unless/until actual block inclusion, which never has to happen for the DOS to work): [6](#0-5) [7](#0-6) 

This is structurally identical to the M-8 pattern: a capacity-limited resource (`pendingOrderIds` / gasless queue slots) with no compulsory fee for occupying it, enabling repeated cheap creation/expiry cycles that starve legitimate participants (`ErrBundleTxQueueFull`) whenever the cap is saturated.

### Impact Explanation
If the queue cap is kept saturated by an attacker, legitimate users' gasless approve/swap transactions are rejected with `ErrBundleTxQueueFull` at `PreAddTx`, denying them access to the gasless flow (KIP-247) that the module exists to provide. This is a functional denial of service against a specific, security-relevant feature (gasless transactions/fee delegation counterparty flow) rather than the general tx-pool DOS already mitigated by the standard `TxPool` account/global slot limits (`ExecSlotsAccount/All`, `NonExecSlotsAccount/All`) that the base pool tests exercise (e.g. `TestTransactionQueueAccountLimiting`) — those base-pool protections do not apply here because the gasless balance/cost check is intentionally bypassed, and the gasless queue cap is a separate, module-level counter that any address (funded or not) can occupy.

### Likelihood Explanation
Likelihood is moderate: the attacker needs to craft transactions that pass `IsApproveTx`/`IsSwapTx` pattern checks (correct method selector, whitelisted token/router, `MaxUint256` approval amount for approve; valid `minAmountOut`/`amountRepay` relation and future deadline for swap), which is public, deterministic logic requiring no privileged information. Depending on the configured `BalanceCheckLevel`, the attacker may not even need real token balance or allowance to have the transaction admitted into the queue slot (only `BalanceCheckLevelStatic` static relations must hold), making the resource occupation essentially free and repeatable indefinitely across throwaway addresses, exactly as in the referenced report's "always re-create at low cost" attack path.

### Recommendation
Consider one or more of:
- Requiring a minimum verified token balance/allowance (or a small bonded deposit) as a precondition to admit a gasless tx into the tracked queue count, independent of `BalanceCheckLevel`.
- Rate-limiting/penalizing per-sender or per-IP submission of gasless-pattern transactions that repeatedly expire via `QueueTimeout` without being promoted/executed.
- Making `MaxBundleTxsInQueue` per-sender in addition to global, so a single actor (or a botnet of throwaway addresses) cannot exhaust the entire shared queue budget with zero-cost transactions.
- Increasing the cost of occupying a queue slot, e.g., only counting transactions toward the queue cap once minimal real-balance/allowance checks (`BalanceCheckLevelTokenBalanceAndAllowance`) pass, regardless of the configured global `BalanceCheckLevel`.

### Proof of Concept
Conceptual PoC (not executed, since only static analysis tools were available in this environment):
1. Deploy/operate a node with default `gasless` config (`MaxBundleTxsInQueue = 200`, `BalanceCheckLevel` at or below `BalanceCheckLevelStatic`).
2. Generate 200+ fresh private keys with zero KAIA and (if `BalanceCheckLevel < TokenBalanceAndAllowance`) zero token balance.
3. For each key, craft a `GaslessApproveTx` (or matching swap) against a whitelisted token/router satisfying `IsApproveTx`/static swap relations, sign, and submit via `SendTx`/RPC.
4. Observe `knownTxs.numQueue()` reach `MaxBundleTxsInQueue`, causing subsequent legitimate `GaslessApproveTx`/`GaslessSwapTx` submissions to fail with `ErrBundleTxQueueFull` in `PreAddTx`.
5. Wait `QueueTimeout` (10s) for the spam entries to be dropped in `PreReset`, then resubmit a fresh batch from new addresses to repeat the DOS indefinitely at near-zero cost.

### Citations

**File:** kaiax/gasless/impl/tx_pool.go (L33-36)
```go
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

**File:** kaiax/gasless/impl/tx_pool.go (L62-72)
```go
func (g *GaslessModule) GetCheckBalance() func(tx *types.Transaction) error {
	return func(tx *types.Transaction) error {
		if approveArgs, ok := decodeApproveTx(tx, g.signer); ok {
			return g.checkBalanceForApprove(approveArgs)
		}
		if swapArgs, ok := decodeSwapTx(tx, g.signer); ok {
			return g.checkBalanceForSwap(swapArgs, tx.Nonce())
		}
		return errors.New("not a gasless transaction") // should not happen because IsModuleTx is called before GetCheckBalance
	}
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

**File:** kaiax/gasless/config.go (L41-54)
```go
	MaxBundleTxsInPendingFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-pending",
		Usage:    "max number of gasless bundle txs in pending queue. Default value is 100. No limit if negative value",
		Value:    100,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-pending"},
		Category: "KAIAX",
	}
	MaxBundleTxsInQueueFlag = &cli.IntFlag{
		Name:     "gasless.max-bundle-txs-in-queue",
		Usage:    "max number of gasless bundle txs in queue. Default value is 200. No limit if negative value",
		Value:    200,
		Aliases:  []string{"kaiax.module.gasless.max-bundle-txs-in-queue"},
		Category: "KAIAX",
	}
```

**File:** kaiax/gasless/config.go (L64-100)
```go
const (
	BalanceCheckLevelStatic                   = iota // relation between amounts and deadline
	BalanceCheckLevelTokenBalanceAndAllowance        // all above + token balance and allowance
	BalanceCheckLevelSwapAmount                      // all above +	amountIn calculated by dex
	BalanceCheckLevelAll                             // all above +	sender code check
)

type GaslessConfig struct {
	// all tokens are allowed if AllowedTokens is nil while all are disallowed if empty slice
	AllowedTokens         []common.Address `toml:",omitempty"`
	Disable               bool
	MaxBundleTxsInPending uint
	MaxBundleTxsInQueue   uint
	BalanceCheckLevel     int
}

func DefaultGaslessConfig() *GaslessConfig {
	return &GaslessConfig{
		AllowedTokens:         nil,
		Disable:               false,
		MaxBundleTxsInPending: 100,
		MaxBundleTxsInQueue:   200,
		BalanceCheckLevel:     BalanceCheckLevelAll,
	}
}

func (cfg *GaslessConfig) ShouldCheckToken() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelTokenBalanceAndAllowance
}

func (cfg *GaslessConfig) ShouldCheckSwapAmount() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelSwapAmount
}

func (cfg *GaslessConfig) ShouldCheckSenderCode() bool {
	return cfg.BalanceCheckLevel >= BalanceCheckLevelAll
}
```

**File:** kaiax/gasless/README.md (L7-27)
```markdown
Gasless transaction (GaslessTx) consists of two types: gasless approve transaction (GaslessApproveTX), and gasless swap transaction (GaslessSwapTx).

Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.

### Transaction pool rules

#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.

#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).
```
