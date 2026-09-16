### Title
Unprivileged sender can exhaust the global gasless bundle-tx queue cap and deny other users' gasless transactions - (File: kaiax/gasless/impl/tx_pool.go)

### Summary
The `GaslessModule.PreAddTx` admission check enforces a single, global counter (`MaxBundleTxsInQueue`, default 200) on how many "bundle" (approve/swap) transactions may sit in the tx-pool queue across *all* senders. Because the counter is not scoped per account and the admission criteria for a qualifying "bundle tx" are cheap to satisfy (any whitelisted/self-deployed ERC20 with a nonzero balance), a single unprivileged sender can flood the queue up to the cap using their own low-value token and minimal gas, permanently (or repeatedly) causing `ErrBundleTxQueueFull` for every other legitimate user trying to submit a gasless approve/swap transaction — the same "abuse a global cap to lock out others" pattern as the referenced `maxContractBalance` finding.

### Finding Description
`PreAddTx` is called for every transaction accepted into the tx pool. For a transaction that matches the gasless bundle-tx pattern (`IsBundleTx`), it checks a single global counter before admission: [1](#0-0) 

`GetMaxBundleTxsInQueue()` defaults to 200 and is a pool-wide (not per-sender) limit: [2](#0-1) [3](#0-2) 

`knownTxs.numQueue()` simply counts every transaction across all senders currently marked `TxStatusQueue`, with no per-account partitioning: [4](#0-3) 

A transaction is recognized as an admissible "approve" bundle tx if it targets a whitelisted ERC20 token, calls `approve(spender, amount)` with `spender == swapRouter` and `amount == MaxUint256`: [5](#0-4) 

The only additional balance-side gate (when `ShouldCheckToken()` is enabled, which is the default `BalanceCheckLevelAll`) is that the sender must hold a nonzero balance of that token: [6](#0-5) 

Critically, the token itself does not need to be a "real"/liquid asset — `AllowedTokens` defaults to `nil`, which the config comment documents as "allow all tokens": [7](#0-6) 

This means an attacker can deploy a trivial ERC20 contract, mint themselves an arbitrary balance, and then submit a sequence of cheap `approve(swapRouter, MaxUint256)` transactions (one per nonce) from a single funded account. Each such transaction passes `IsApproveTx`/`checkBalanceForApprove` and is admitted into `knownTxs` as `TxStatusQueue`, incrementing the global counter. Once `numQueue()` reaches `MaxBundleTxsInQueue`, `PreAddTx` unconditionally rejects *any other user's* gasless approve/swap transaction with `ErrBundleTxQueueFull`, regardless of that user's own account/balance/legitimacy — mirroring the reported analog where a whale/attacker races an innocuous, cheap operation to saturate a shared cap before a victim's legitimate operation can be admitted.

The attacker's queued transactions are only cleared after `QueueTimeout` (10s) via `PreReset`, and can be trivially resubmitted (new nonces, or simply waiting out the 10s window and refilling) to sustain the denial indefinitely at negligible cost, since the requisite ERC20 and its balance are entirely attacker-controlled and require no real capital, only gas for `approve` calls.

### Impact Explanation
This directly denies the gasless-transaction feature to any other user reaching the affected node: their fee-delegated "gas-less" approve/swap bundle submissions will be rejected with `ErrBundleTxQueueFull` at the tx-pool admission layer, before they can ever be considered for inclusion, even though the victim's own transaction and balances are entirely valid. This is a persistent, cheaply renewable denial-of-service against a specific in-scope module (gasless), directly matching the accepted bug class ("deny others by racing/saturating a shared admission cap"), and can be sustained by a single low-cost account rather than requiring a whale-sized deposit as in the original report.

### Likelihood Explanation
High likelihood: the attack requires only (1) deploying a minimal ERC20 contract and minting tokens to self (cheap, one-time), and (2) repeatedly sending `approve(swapRouter, MaxUint256)` transactions from that account, which cost only base transaction gas. No special privileges, front-running precision, or large capital are needed — a slow, steady stream of ~200 cheap transactions (refreshed every ~10s as the queue window rolls) is sufficient to keep the global cap saturated.

### Recommendation
Scope the `MaxBundleTxsInQueue` (and `MaxBundleTxsInPending`) admission check per-sender (or per unique token/route) rather than as a single global counter, and/or require a stronger sender-side cost/reputation signal (e.g., minimum real-value token allow-list rather than "all tokens", or per-account slot reservation) so that one account cannot monopolize the shared bundle-tx queue capacity and starve other legitimate senders.

### Proof of Concept
1. Deploy a minimal ERC20 token contract; mint balance to attacker EOA `A`.
2. Ensure `A` is registered/whitelisted implicitly since `AllowedTokens` defaults to `nil` ("all tokens allowed").
3. From `A`, submit `MaxBundleTxsInQueue` (default 200) sequential-nonce transactions calling `approve(swapRouterAddr, type(uint256).max)` on the attacker's token — each satisfies `IsApproveTx` (`kaiax/gasless/impl/getter.go:74-86`) and `checkBalanceForApprove` (`kaiax/gasless/impl/tx_pool.go:74-100`), and each is admitted with `TxStatusQueue`, incrementing `knownTxs.numQueue()` (`kaiax/gasless/impl/tx_counter.go:114-122`).
4. Once `numQueue() >= 200`, any other legitimate user `B` submitting a valid `approve`/`swapForGas` gasless transaction receives `ErrBundleTxQueueFull` from `PreAddTx` (`kaiax/gasless/impl/tx_pool.go:46-49`), regardless of `B`'s own balances/allowances.
5. Repeat step 3 every ~10 seconds (`QueueTimeout`) with fresh nonces to sustain the denial indefinitely at minimal gas cost.

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

**File:** kaiax/gasless/impl/tx_pool.go (L74-100)
```go
func (g *GaslessModule) checkBalanceForApprove(approveArgs *ApproveArgs) error {
	token := approveArgs.Token
	bc := backends.NewBlockchainContractBackend(g.Chain, nil, nil)

	if g.GaslessConfig.ShouldCheckSenderCode() {
		if g.getCurrentHasCode(approveArgs.Sender) {
			return errors.New("sender with code is not allowed")
		}
	}

	if g.GaslessConfig.ShouldCheckToken() {
		tokenContract, err := sc_erc20.NewERC20(token, bc)
		if err != nil {
			return err
		}

		// tx.token.balanceOf(sender) > 0
		tokenBalance, err := tokenContract.BalanceOf(nil, approveArgs.Sender)
		if err != nil {
			return err
		}
		if tokenBalance.Sign() <= 0 {
			return fmt.Errorf("insufficient sender token balance: token=%s, have=%s, want=nonzero", token.Hex(), tokenBalance.String())
		}
	}
	return nil
}
```

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

**File:** kaiax/gasless/config.go (L71-78)
```go
type GaslessConfig struct {
	// all tokens are allowed if AllowedTokens is nil while all are disallowed if empty slice
	AllowedTokens         []common.Address `toml:",omitempty"`
	Disable               bool
	MaxBundleTxsInPending uint
	MaxBundleTxsInQueue   uint
	BalanceCheckLevel     int
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
