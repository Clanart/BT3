### Title
Gasless bundle-tx admission queue (`MaxBundleTxsInQueue`) can be exhausted by cheap attacker transactions, blocking legitimate gasless users - (File: `kaiax/gasless/impl/tx_pool.go`)

### Summary
The `gasless` kaiax module enforces a fixed, chain-wide cap on the number of "bundle" (approve/swap) transactions that may sit in the tx-pool queue at once, via `GaslessConfig.MaxBundleTxsInQueue` (default 200). Any unprivileged sender can craft transactions that satisfy the module's static `IsApproveTx`/`IsSwapTx` predicates at near-zero cost and flood this shared counter before honest users' gasless transactions are submitted, causing the honest transactions to be rejected from the pool with `ErrBundleTxQueueFull`. This mirrors the reported OpenQ bug class: a bounded whitelisted-resource counter that any low-cost caller can fill first, permanently blocking legitimate use of the feature.

### Finding Description
When a transaction enters the pool, `TxPool.add` invokes each registered pool module's `PreAddTx` hook before running per-tx validation and balance checks: [1](#0-0) 

For the gasless module, `PreAddTx` classifies the tx via `IsBundleTx` (`IsModuleTx` → `IsApproveTx` || `IsSwapTx`) and, if it looks like a bundle tx, immediately increments the shared `knownTxs` queue counter and rejects the tx if the counter is already at `MaxBundleTxsInQueue`: [2](#0-1) 

`IsApproveTx` only requires that `tx.to` is a whitelisted ERC20, the call data is `approve(spender, amount)`, `spender` is the whitelisted `SwapRouter`, and `amount == MaxUint256` — no non-trivial balance/allowance check is part of this classification step: [3](#0-2) 

The deeper balance validation (`checkBalanceForApprove` / `checkBalanceForSwap`, requiring nonzero token balance) happens later, via `GetCheckBalance`, and is only enforced when `BalanceCheckLevel >= BalanceCheckLevelTokenBalanceAndAllowance`: [4](#0-3) 

Because a whitelisted ERC-20's `approve()` call can be issued by any address regardless of its token balance in principle, and because acquiring a nonzero (dust) balance of a whitelisted token is trivial/near-free for common tokens, an attacker can mint many disposable EOAs, fund them with only enough KAIA to pay gas, obtain a dust amount of a whitelisted token, and submit `approve(swapRouter, MaxUint)` transactions from each. Each such tx is classified as a valid bundle tx by `IsApproveTx` and is admitted into `knownTxs` at `TxStatusQueue` by `PreAddTx`, incrementing `numQueue()` toward `MaxBundleTxsInQueue` (default 200, configurable via `gasless.max-bundle-txs-in-queue`): [5](#0-4) 

Once the counter reaches the cap, every subsequent legitimate gasless approve/swap transaction submitted by honest users is rejected at admission with `ErrBundleTxQueueFull`: [6](#0-5) 

Queue entries only expire after `QueueTimeout`/`KnownTxTimeout` (10s/30s), so the attacker only needs to keep resubmitting fresh cheap approve-shaped transactions from new senders/nonces to sustain the denial-of-service indefinitely at minimal recurring cost: [7](#0-6) 

### Impact Explanation
This is a Medium-severity denial-of-service against the gasless/sponsored-transaction feature: legitimate users relying on the whitelisted gasless swap flow (a public, unprivileged transaction path anyone can use to pay gas via a whitelisted ERC20 swap) can be locked out of submitting their approve/swap bundle transactions to the pool because a cheap, unprivileged attacker filled the shared, capacity-limited `knownTxs` queue counter first. This directly parallels the reported bug class — a bounded, whitelist-adjacent resource that legitimate participants share with the public and that an attacker can exhaust at negligible cost, blocking honest use of the feature.

### Likelihood Explanation
The attack requires only: (1) gas funds for many small transactions from disposable accounts, and (2) a dust balance of any single whitelisted token to satisfy `IsApproveTx`'s static shape and, if `BalanceCheckLevel` requires it, the nonzero-balance check. Both are cheap and readily obtainable for widely-used whitelisted tokens, and the default cap (200) is a moderate, quickly reachable size for a determined but unprivileged actor. No special privileges, validator collusion, or node compromise are needed — it is reachable purely from public transaction submission.

### Recommendation
Do not let the `PreAddTx` classification alone reserve a slot in the bounded bundle queue based on cheap, unauthenticated shape-matching. Options include:
- Perform the balance/allowance check for approve/swap bundle candidates before admitting them into `knownTxs`'s queue-counted set, so dust-balance/no-real-intent transactions cannot occupy queue slots.
- Rate-limit or account-partition the bundle-queue admission (e.g., cap bundle-tx admissions per sender/time-window) so a single actor spinning up many throwaway accounts cannot monopolize the shared `MaxBundleTxsInQueue` capacity.
- Shrink `KnownTxTimeout`/`QueueTimeout` or make eviction/backpressure adaptive to sustained flooding, and prioritize evicting cheap/dust-balance entries over genuine, well-funded gasless requests.

### Proof of Concept
1. Attacker acquires dust (e.g., 1 wei) balance of a token listed in `g.allowedTokens` for the active `GaslessSwapRouter`.
2. Attacker generates `N = MaxBundleTxsInQueue` (default 200) fresh EOAs, each funded with only enough KAIA for gas.
3. From each EOA, attacker submits `token.approve(swapRouter, type(uint256).max)`, matching `IsApproveTx`'s A1–A4 conditions exactly: [3](#0-2) 
4. Each transaction passes `TxPool.add`'s module dispatch and hits `GaslessModule.PreAddTx`, which adds it to `knownTxs` as `TxStatusQueue` and increments `numQueue()`: [8](#0-7) 
5. Once `numQueue() >= MaxBundleTxsInQueue`, a legitimate user's genuine gasless approve/swap transaction submitted to the same pool is rejected with `ErrBundleTxQueueFull` before further validation even runs, denying them use of the gasless feature.
6. Attacker repeats with fresh throwaway accounts every `QueueTimeout`/`KnownTxTimeout` window to sustain the block indefinitely at minimal recurring cost.

### Citations

**File:** blockchain/tx_pool.go (L1139-1148)
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
```

**File:** kaiax/gasless/impl/tx_pool.go (L33-35)
```go
	QueueTimeout   = 10 * time.Second // bundle tx is removed from txpool.queue after QueueTimeout
	PendingTimeout = 10 * time.Second // bundle tx is removed from txpool.pending after PendingTimeout
	KnownTxTimeout = 30 * time.Second // bundle tx is removed from knownTxs after KnownTxTimeout
```

**File:** kaiax/gasless/impl/tx_pool.go (L38-60)
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

func (g *GaslessModule) IsModuleTx(tx *types.Transaction) bool {
	if tx == nil {
		return false
	}
	return g.IsApproveTx(tx) || g.IsSwapTx(tx)
}
```

**File:** kaiax/gasless/impl/tx_pool.go (L62-99)
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

**File:** kaiax/gasless/impl/errors.go (L24-39)
```go
var (
	ErrInitUnexpectedNil         = errors.New("unexpected nil during module init")
	ErrGSRNotInstalled           = errors.New("Gasless swap router contract not installed")
	ErrDecodeSwapTx              = errors.New("failed to decode swap transaction")
	ErrSwapTxInvalid             = errors.New("swap transaction is not valid")
	ErrDecodeApproveTx           = errors.New("failed to decode approve transaction")
	ErrApproveTxInvalid          = errors.New("approve transaction is not valid")
	ErrDifferentSenders          = errors.New("approve and swap transactions have different senders")
	ErrDifferentTokens           = errors.New("approve transaction is for different token than swap transaction")
	ErrInsufficientApproveAmount = errors.New("approve transaction approves insufficient amount")
	ErrNonSequentialNonce        = errors.New("approve and swap transactions have non-sequential nonces")
	ErrApproveNonceNotCurrent    = errors.New("approve transaction nonce is not current")
	ErrSwapNonceNotCurrent       = errors.New("swap transaction nonce is not current")
	ErrIncorrectRepayAmount      = errors.New("swap transaction has incorrect amountRepay")
	ErrUnableToAddKnownBundleTx  = errors.New("cannot add known bundle tx during cooldown")
	ErrBundleTxQueueFull         = errors.New("bundle tx queue is full")
```
