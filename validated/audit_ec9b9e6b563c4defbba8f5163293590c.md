### Title
DoS of the Gasless fee-delegation feature via global `MaxBundleTxsInQueue` exhaustion in `GaslessModule.PreAddTx` - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
`GaslessModule.PreAddTx` enforces a single, node-wide counter (`MaxBundleTxsInQueue`, default 200) shared by every sender to gate how many "gasless bundle" candidate transactions (approve/swap) may sit in the queue. Because the classification `IsApproveTx` only checks that the token is whitelisted, the spender is the whitelisted `swapRouter`, and the amount equals `MaxUint256` — with no balance/allowance precondition enforced at this stage — an unprivileged attacker can cheaply mint this classification from an unlimited number of throwaway EOAs and saturate the shared counter, permanently starving legitimate users' gasless swap transactions from ever being recognized/queued as bundle txs.

### Finding Description
`PreAddTx` is called for every incoming transaction and decides whether a tx should be tracked as a gasless "bundle" tx: [1](#0-0) 

The check `g.IsBundleTx(tx)` resolves to `IsModuleTx`, which is true if the tx satisfies `IsApproveTx` OR `IsSwapTx`: [2](#0-1) 

`isApproveTx` only requires: token is in `allowedTokens` (whitelist populated from on-chain GSR registry), `spender == swapRouter`, and `amount == MaxUint256`: [3](#0-2) 

Critically, no balance, no allowance, no fee/repayment context is required to pass this classification — any address can call `approve(swapRouter, type(uint256).max)` on a whitelisted ERC20 (or even without holding any token balance, since ERC20 `approve()` does not require a balance) and the resulting tx is classified as a gasless bundle tx by `PreAddTx`, consuming one of the `MaxBundleTxsInQueue` global slots: [4](#0-3) 

Once `numQueue() >= GetMaxBundleTxsInQueue()`, `ErrBundleTxQueueFull` is returned for every subsequent classified tx, from any sender: [5](#0-4) 

The queue capacity is a single global counter, not scoped per sender: [6](#0-5) 

This is structurally identical to the OpenQ report: a shared, limited-capacity, whitelist-gated resource (`TOKEN_ADDRESS_LIMIT` in OpenQ vs. `MaxBundleTxsInQueue` in Kaia) can be filled by an unprivileged, low-cost action (funding fake ERC20s in OpenQ vs. submitting throwaway approve txs against whitelisted tokens in Kaia), permanently blocking legitimate users from a feature meant to be broadly available (funding a bounty vs. using gasless fee delegation).

### Impact Explanation
Any unprivileged party who can submit ordinary transactions to the pool can drive `numQueue()` to `MaxBundleTxsInQueue` using cheap `approve` calls against a whitelisted token and the whitelisted `swapRouter`, from arbitrarily many funded-with-dust EOAs. Once full, legitimate users' genuine gasless approve/swap transactions are rejected at admission with `ErrBundleTxQueueFull` and never get a chance to be evaluated as executable bundles, denying them the gasless fee-delegation feature entirely. This is a availability/DoS impact on a core public-facing feature (gasless transactions), reachable purely via public RPC transaction submission.

### Likelihood Explanation
Likelihood is high: the attack requires only (a) knowledge of one whitelisted token address and the `swapRouter` address (both publicly queryable via `GaslessInfo()` RPC), and (b) enough KAIA to pay gas for cheap `approve()` calls from many addresses. No special privileges, balances, or preconditions on the attacker's token holdings are required because `isApproveTx` does not check balance and `PreAddTx` does not call `GetCheckBalance` before admitting the tx into `knownTxs`.

### Recommendation
Scope the `MaxBundleTxsInQueue`/`MaxBundleTxsInPending` admission limits per-sender (or per unique sender+token) rather than as a single global counter, and/or require a lightweight balance/allowance precondition (or a bonded/staked cost) before a candidate approve tx is allowed to consume a global queue slot. Additionally, consider rate-limiting bundle-tx classification per sender address within `PreAddTx`.

### Proof of Concept
1. Query `GaslessInfo()` RPC to obtain the currently whitelisted `swapRouter` and an `allowedTokens` entry. [7](#0-6) 
2. Generate `N = MaxBundleTxsInQueue` (default 200) fresh throwaway private keys, fund each with minimal KAIA for gas.
3. From each key, submit `approve(swapRouter, type(uint256).max)` on the whitelisted token address. Each such tx passes `isApproveTx` and is admitted into `knownTxs` with `TxStatusQueue` via `PreAddTx`. [8](#0-7) 
4. Once 200 such txs are queued, any legitimate user's genuine approve/swap gasless transaction submitted afterward is rejected with `ErrBundleTxQueueFull`, denying them gasless fee delegation until the attacker's txs are evicted (`QueueTimeout`), at which point the attacker can resubmit fresh ones to sustain the DoS.

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

**File:** kaiax/gasless/impl/tx_pool.go (L55-60)
```go
func (g *GaslessModule) IsModuleTx(tx *types.Transaction) bool {
	if tx == nil {
		return false
	}
	return g.IsApproveTx(tx) || g.IsSwapTx(tx)
}
```

**File:** kaiax/gasless/impl/getter.go (L79-86)
```go
func (g *GaslessModule) isApproveTx(args *ApproveArgs) bool {
	g.gaslessInfoMu.RLock()
	defer g.gaslessInfoMu.RUnlock()

	return g.allowedTokens[args.Token] && // A1
		g.swapRouter == args.Spender && // A3
		args.Amount.Cmp(abi.MaxUint256) == 0 // A4
}
```

**File:** kaiax/gasless/impl/errors.go (L38-39)
```go
	ErrUnableToAddKnownBundleTx  = errors.New("cannot add known bundle tx during cooldown")
	ErrBundleTxQueueFull         = errors.New("bundle tx queue is full")
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

**File:** kaiax/gasless/impl/api.go (L135-149)
```go
func (s *GaslessAPI) GaslessInfo() *GaslessInfoResult {
	s.b.gaslessInfoMu.RLock()
	defer s.b.gaslessInfoMu.RUnlock()

	at := []common.Address{}
	for addr := range s.b.allowedTokens {
		at = append(at, addr)
	}
	return &GaslessInfoResult{
		IsDisabled:    s.b.IsDisabled(),
		SwapRouter:    s.b.swapRouter,
		AllowedTokens: at,
		MaxBundleTxs:  s.b.GetMaxBundleTxsInPending(),
	}
}
```
