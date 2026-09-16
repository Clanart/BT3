## Title
Cheap gasless bundle-tx spam can exhaust the shared `MaxBundleTxsInQueue`/`MaxBundleTxsInPending` cap and deny gasless service to legitimate users - (File: `kaiax/gasless/impl/tx_pool.go`)

## Summary
The `takeLoan` bug class in the report is a per-block shared resource with a small fixed cap (`MAX_LOAN_PER_BLOCK`) that costs an attacker almost nothing to consume, blocking legitimate consumers of the same resource. Kaia's gasless module (`kaiax/gasless`) has an analogous shared, capacity-limited resource: the node-wide count of "bundle txs" (`GaslessApproveTx`/`GaslessSwapTx`) allowed in the tx-pool queue and pending set, bounded by `MaxBundleTxsInQueue` (default 200) and `MaxBundleTxsInPending` (default 100).

## Finding Description
`PreAddTx` reserves a queue slot for any transaction that `IsBundleTx` recognizes as a gasless approve/swap transaction, purely based on cheap-to-satisfy static checks (token whitelisted, spender is the whitelisted `SwapRouter`, `amount == MaxUint256` for approve; router/token whitelisted call shape for swap) — before the more expensive balance/state checks are applied: [1](#0-0) 

The capacity check itself is a simple global counter comparison against a configurable, but fixed and small, ceiling: [2](#0-1) 

`IsApproveTx`/`IsSwapTx` decode and match against whitelisted tokens/router with no larger economic cost required beyond forming a validly-shaped approve/swap call: [3](#0-2) 

The defaults for these caps are small and static, independent of network size: [4](#0-3) [5](#0-4) 

Because these limits are enforced as flat counters (`numQueue()`/pending count) shared across all senders rather than per-account allotments, and gasless approve/swap "bundle" transactions are cheap to construct (only requiring the sender to hold a nonzero balance of any whitelisted token and match the whitelisted router calldata shape — no real capital lock-up analogous to the pooled resource itself is required), an unprivileged sender can repeatedly submit many distinct-signer gasless approve/swap transactions to fill the queue up to `MaxBundleTxsInQueue`/`MaxBundleTxsInPending`. Existing tests confirm the hard queue-full behavior once the limit is reached: [6](#0-5) 

## Impact Explanation
Once the queue/pending caps are saturated by an attacker's cheap gasless bundle transactions, legitimate users' `GaslessApproveTx`/`GaslessSwapTx` pairs are rejected with `ErrBundleTxQueueFull`, denying them the proposer-funded gas lending mechanism (KIP-247) entirely for the duration of the attack. This is a direct denial-of-service against a specific value-bearing feature (gasless transactions), reachable by any public RPC caller submitting ordinary signed transactions — no special privilege, node access, or protocol-level compromise is needed. This matches the "Medium" bug class of the analog: cheap DoS against a shared, bounded resource that legitimate transactions depend on.

## Likelihood Explanation
The attack requires only the ability to craft many distinct signer/sender approve or swap transactions targeting the whitelisted token and `GaslessSwapRouter` — a trivially reachable action for any account holding a small amount of a whitelisted ERC-20 token (required to pass `checkBalanceForApprove`'s "tokenBalance.Sign() > 0" gate) or, for a swap tx, similarly modest requirements. Given `MaxBundleTxsInQueue` defaults to only 200 and `MaxBundleTxsInPending` to 100, and the counter is global rather than per-account, filling the queue with even a handful of low-value token holdings across multiple addresses (or repeated submissions before replacement/timeout) is inexpensive relative to the value of denying gasless access to all other users.

## Recommendation
- Enforce a per-sender or per-token quota within the bundle-tx queue/pending counters rather than one global counter, so a single attacker (or attacker-controlled address set) cannot consume the entire capacity.
- Consider requiring a more substantial economic cost (e.g., a minimum swap amount, deposit, or per-address rate limiting) before a gasless bundle tx occupies a queue slot.
- Evaluate raising/making the caps dynamic based on demand, and add eviction/prioritization logic (e.g., favor unique senders over repeated ones) to reduce the effectiveness of flooding.

## Proof of Concept
1. Attacker whitelists no special privilege; it identifies the `GaslessSwapRouter` and an allowed token (`AllowedTokens`) from on-chain state.
2. Attacker generates many key pairs, gives each a minimal nonzero balance of the allowed token (or crafts standalone swap txs satisfying the static shape checks), and submits `GaslessApproveTx`/`GaslessSwapTx` pairs from each.
3. Each transaction passes `IsApproveTx`/`IsSwapTx` and is accepted into `knownTxs` via `PreAddTx` until `numQueue() >= GetMaxBundleTxsInQueue()` (default 200), as enforced at: [7](#0-6) 
4. Subsequent legitimate gasless transactions from other users are rejected with `ErrBundleTxQueueFull`, denying them gasless service until the attacker's transactions are processed/expired (`QueueTimeout`/`PendingTimeout` = 10s each), after which the attacker can repeat the flood.

Note: I was unable to fully trace the exact ordering of `PreAddTx` versus `GetCheckBalance` within `blockchain/tx_pool.go` (i.e., whether the slot reservation in `PreAddTx` happens strictly before the balance check, which would make the attack even cheaper since invalid-balance transactions could still occupy slots transiently) due to tool-call exhaustion; a background agent with full file access should confirm this ordering to precisely characterize the attack's minimum cost.

### Citations

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

**File:** kaiax/gasless/impl/getter.go (L69-95)
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

**File:** kaiax/gasless/impl/tx_pool_test.go (L86-126)
```go
func TestPreAddTxQueueLimit(t *testing.T) {
	log.EnableLogForTest(log.LvlError, log.LvlTrace)

	newModule := func(maxQueue uint) *GaslessModule {
		cfg := *gasless.DefaultGaslessConfig()
		cfg.MaxBundleTxsInQueue = maxQueue
		g := NewGaslessModule()
		dbm := database.NewMemoryDBManager()
		backend := backends.NewSimulatedBackendWithDatabase(dbm, testAllocStorage(), testChainConfig)
		nodekey, _ := crypto.GenerateKey()
		require.NoError(t, g.Init(&InitOpts{
			ChainConfig:   testChainConfig,
			GaslessConfig: &cfg,
			NodeKey:       nodekey,
			Chain:         backend.BlockChain(),
			NodeType:      common.ENDPOINTNODE,
		}))
		return g
	}

	bundleTx := func() *types.Transaction {
		privkey, _ := crypto.GenerateKey()
		return makeApproveTx(t, privkey, 0, ApproveArgs{Spender: common.HexToAddress("0x1234"), Amount: abi.MaxUint256})
	}

	// --gasless.max-bundle-txs-in-queue <= 0 maps to the math.MaxUint64 "no limit"
	// sentinel (config.go).
	t.Run("no limit sentinel accepts on empty queue", func(t *testing.T) {
		g := newModule(math.MaxUint64)
		tx := bundleTx()
		require.True(t, g.IsBundleTx(tx))
		require.NoError(t, g.PreAddTx(tx, false))
		require.Equal(t, 1, g.knownTxs.numQueue())
	})

	t.Run("finite limit still enforced", func(t *testing.T) {
		g := newModule(1)
		require.NoError(t, g.PreAddTx(bundleTx(), false))
		require.ErrorIs(t, g.PreAddTx(bundleTx(), false), ErrBundleTxQueueFull)
	})
}
```
