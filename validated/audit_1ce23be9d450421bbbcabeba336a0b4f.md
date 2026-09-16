## Title
Unbounded auction bid pool due to permissive default `MaxBidPoolSize`, allowing unprivileged bidders to exhaust node memory - (File: `kaiax/auction/config.go`, `kaiax/auction/impl/bid_pool.go`)

### Summary
Kaia's core `blockchain.TxPool` already implements the exact Geth-style mitigations the external report recommends: a hard per-transaction size cap (`MaxTxDataSize` = 4×32KB = 128KB) [1](#0-0)  and slot-based global/per-account admission limits (`ExecSlotsAccount/All`, `NonExecSlotsAccount/All`, default 16/4096/64/1024) enforced during promotion and eviction [2](#0-1) [3](#0-2) . The `gasless` module also enforces configurable per-pool bundle-tx limits with sane defaults (100/200) [4](#0-3) . None of these mempools have the unbounded-memory problem described in the report.

However, the newer `kaiax/auction` bid pool - reachable directly by any unprivileged RPC caller acting as an "auction bidder" via `auction_submitBid` - has the same class of weakness: the pool's overall size cap defaults to effectively unlimited, and admission to that cap is not rate-limited at the RPC layer.

### Finding Description
`BidPool.AddBid` is invoked directly from the public RPC method `AuctionAPI.SubmitBid` (`auction_submitBid`) without any additional throttling: [5](#0-4) .

Each accepted bid is size-capped per item (`BidTxMaxDataSize = 64 * 1024`, i.e. 64KB) and gas-capped (`BidTxMaxCallGasLimit`) [6](#0-5) , so a single bid cannot be arbitrarily large. But the *total number* of bids the pool will accept is governed solely by `maxBidPoolSize`, whose default is `math.MaxInt64` — i.e., no practical limit unless the node operator explicitly overrides it with `--auction.max-bid-pool-size`: [7](#0-6) [8](#0-7) .

`insertBid` only rejects a new bid once `len(bp.bidMap) >= bp.maxBidPoolSize`, and this check only applies to bids that target a *new* `(blockNumber, targetTxHash)` slot; it does not otherwise throttle the rate of insertion: [9](#0-8) . The only other admission control is `senderHasDifferentWinner`, which merely prevents the *same* sender address from holding two different winning targets simultaneously — it does not limit the number of distinct senders/targets an attacker can use to grow `bidMap`, `bidTargetMap`, and `bidWinnerMap`.

The peer-level rate limiter (`bidsPerSecondPerPeer = 300`) referenced in the struct only guards inbound p2p bid gossip (`handleBidMsg`), not the direct RPC path used by `SubmitBid` → `AddBid`, so a local RPC client is not subject to that throttle.

Because the module's default `MaxBidPoolSize` sentinel is `math.MaxInt64`, a consensus node that has not explicitly hardened this flag effectively runs the bid pool with no cap on bid count, mirroring the "no maximum limit on transaction count for the pool" defect described in the external report, just applied to the auction bid pool instead of the classic tx pool.

### Impact Explanation
Any RPC caller with access to `auction_submitBid` (a role explicitly in-scope as "auction bidder") can submit a stream of distinct, individually-valid bids (each up to 64KB of `Data`, plus RLP-encoded `TargetTxRaw`, signatures, etc.) for many different (blockNumber, targetTxHash) pairs. With the default `MaxBidPoolSize`, none of these are rejected for pool-capacity reasons, so `bidMap`/`bidTargetMap`/`bidWinnerMap` grow without bound until the node runs out of memory (OOM), taking down a consensus/proposer node — the same failure mode (unbounded mempool memory consumption → crash) documented in the external report. This affects consensus-node availability, which can degrade or halt auction settlement and, transitively, block production if enough consensus nodes have auction enabled and are hit.

### Likelihood Explanation
Likelihood is Medium: exploitation requires (a) the node operator not having overridden the permissive default `MaxBidPoolSize`, and (b) the auction module being enabled (it's disabled by default for non-consensus nodes, but enabled by default for consensus nodes unless `auction.disable` is set) [10](#0-9) . Given the default is `math.MaxInt64`, most deployments that don't explicitly tune this flag are exposed, and the attack requires only crafting many syntactically valid bids (each needing a decodable target tx and valid searcher/auctioneer signatures) — no special privilege or on-chain cost is required beyond bid construction.

### Recommendation
- Change the default `MaxBidPoolSize` in `kaiax/auction/config.go` from `math.MaxInt64` to a small, safe finite value (e.g., a few thousand, sized against `BidTxMaxDataSize` similar to how the core tx pool bounds `ExecSlotsAll+NonExecSlotsAll` against `MaxTxDataSize`).
- Enforce a per-sender or per-RPC-caller bid-submission rate limit for the `auction_submitBid` RPC path (the current `peerRateLimiter` only protects p2p ingestion in `handleBidMsg`, not the RPC-invoked `AddBid`).
- Consider bounding total bytes held in the bid pool (sum of bid data sizes), not just bid count, to fully mirror the "slot accounting" approach recommended for the base tx pool.

### Proof of Concept
1. Deploy/point at a Kaia consensus node with default configuration (`auction.max-bid-pool-size` not set, i.e., `math.MaxInt64`), auction module enabled.
2. As an unprivileged RPC client, repeatedly call `auction_submitBid` with distinct, validly-signed `BidInput` payloads: vary `TargetTxRaw`/`TargetTxHash` (any decodable, distinct target transaction) and `Sender` per request, each carrying close to `BidTxMaxDataSize` (64KB) in `Data`.
3. Because `insertBid` only errors when `len(bp.bidMap) >= maxBidPoolSize` (effectively unreachable at `math.MaxInt64`), each request succeeds and is retained in `bp.bidMap`/`bp.bidTargetMap`/`bp.bidWinnerMap` until the corresponding block is processed by `removeOldBids`.
4. Sending bids fast enough, or targeting many future blocks within the `allowFutureBlock` window, accumulates memory continuously; sustained submission drives the process toward OOM.

### Citations

**File:** blockchain/tx_pool.go (L51-62)
```go
	// txSlotSize is used to calculate how many data slots a single transaction
	// takes up based on its size. The slots are used as DoS protection, ensuring
	// that validating a new transaction remains a constant operation (in reality
	// O(maxslots), where max slots are 4 currently).
	txSlotSize = 32 * 1024

	// MaxTxDataSize is the maximum size a single transaction can have. This field has
	// non-trivial consequences: larger transactions are significantly harder and
	// more expensive to propagate; larger transactions also take more resources
	// to validate whether they fit into the pool or not.
	// TODO-Kaia: Change the name to clarify what it means. It means the max length of the transaction.
	MaxTxDataSize = 4 * txSlotSize // 128KB
```

**File:** blockchain/tx_pool.go (L161-191)
```go
	ExecSlotsAccount    uint64 // Number of executable transaction slots guaranteed per account
	ExecSlotsAll        uint64 // Maximum number of executable transaction slots for all accounts
	NonExecSlotsAccount uint64 // Maximum number of non-executable transaction slots permitted per account
	NonExecSlotsAll     uint64 // Maximum number of non-executable transaction slots for all accounts

	KeepLocals bool          // Disables removing timed-out local transactions
	Lifetime   time.Duration // Maximum amount of time non-executable transaction are queued

	NoAccountCreation            bool // Whether account creation transactions should be disabled
	EnableSpamThrottlerAtRuntime bool // Enable txpool spam throttler at runtime

	BlobStorageConfig *BlobStorageConfig // Blob storage configuration
}

// DefaultTxPoolConfig contains the default configurations for the transaction
// pool.
var DefaultTxPoolConfig = TxPoolConfig{
	Journal:         "transactions.rlp",
	JournalInterval: time.Hour,

	PriceLimit: 1,
	PriceBump:  10,

	ExecSlotsAccount:    16,
	ExecSlotsAll:        4096,
	NonExecSlotsAccount: 64,
	NonExecSlotsAll:     1024,

	KeepLocals: false,
	Lifetime:   5 * time.Minute,
}
```

**File:** blockchain/tx_pool.go (L1779-1849)
```go
	// If the pending limit is overflown, start equalizing allowances. The running
	// total is maintained incrementally so this is O(1) instead of O(N_senders).
	pending := pool.pendingCount

	if pending > pool.config.ExecSlotsAll {
		pendingBeforeCap := pending
		// Assemble a spam order to penalize large transactors first
		spammers := prque.New()
		for addr, list := range pool.pending {
			// Only evict transactions from high rollers
			if !pool.locals.contains(addr) && uint64(list.Len()) > pool.config.ExecSlotsAccount {
				spammers.Push(addr, int64(list.Len()))
			}
		}
		// Gradually drop transactions from offenders
		offenders := []common.Address{}
		for pending > pool.config.ExecSlotsAll && !spammers.Empty() {
			// Retrieve the next offender if not local address
			offender, _ := spammers.Pop()
			offenders = append(offenders, offender.(common.Address))

			// Equalize balances until all the same or below threshold
			if len(offenders) > 1 {
				// Calculate the equalization threshold for all current offenders
				threshold := pool.pending[offender.(common.Address)].Len()

				// Iteratively reduce all offenders until below limit or threshold reached
				for pending > pool.config.ExecSlotsAll && pool.pending[offenders[len(offenders)-2]].Len() > threshold {
					for i := 0; i < len(offenders)-1; i++ {
						list := pool.pending[offenders[i]]
						capped := list.Cap(list.Len() - 1)
						pool.pendingCount -= uint64(len(capped))
						for _, tx := range capped {
							// Drop the transaction from the global pools too
							hash := tx.Hash()
							pool.all.Remove(hash)
							pool.priced.Removed()

							// Update the account nonce to the dropped transaction
							pool.updatePendingNonce(offenders[i], tx.Nonce())
							logger.Trace("Removed fairness-exceeding pending transaction", "hash", hash)
						}
						pending--
					}
				}
			}
		}
		// If still above threshold, reduce to limit or min allowance
		if pending > pool.config.ExecSlotsAll && len(offenders) > 0 {
			for pending > pool.config.ExecSlotsAll && uint64(pool.pending[offenders[len(offenders)-1]].Len()) > pool.config.ExecSlotsAccount {
				for _, addr := range offenders {
					list := pool.pending[addr]
					capped := list.Cap(list.Len() - 1)
					pool.pendingCount -= uint64(len(capped))
					for _, tx := range capped {
						// Drop the transaction from the global pools too
						hash := tx.Hash()
						pool.all.Remove(hash)
						pool.priced.Removed()

						// Update the account nonce to the dropped transaction
						pool.updatePendingNonce(addr, tx.Nonce())
						logger.Trace("Removed fairness-exceeding pending transaction", "hash", hash)
					}
					pending--
				}
			}
		}
		pendingRateLimitCounter.Inc(int64(pendingBeforeCap - pending))
	}
	// If we've queued more transactions than the hard limit, drop oldest ones.
```

**File:** kaiax/gasless/config.go (L41-88)
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
	BalanceCheckLevelFlag = &cli.IntFlag{
		Name:     "gasless.balance-check-level",
		Usage:    "balance check level: 0=static checks, 1=token balance and allowance, 2=swap amount, 3=all",
		Value:    BalanceCheckLevelAll,
		Aliases:  []string{"kaiax.module.gasless.balance-check-level"},
		Category: "KAIAX",
	}
)

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
```

**File:** kaiax/auction/impl/api.go (L118-142)
```go
func (api *AuctionAPI) SubmitBid(ctx context.Context, bidInput BidInput) RPCOutput {
	numBidRequestCounter.Inc(1)
	if api.a.IsDisabled() {
		return makeRPCOutput(EMPTY_HASH, auction.ErrAuctionDisabled)
	}

	//  1. directly send target transaction
	targetTx, errTxDecode := toTx(bidInput.TargetTxRaw)
	if errTxDecode != nil {
		return makeRPCOutput(EMPTY_HASH, errTxDecode)
	}
	if targetTx.Hash() != bidInput.TargetTxHash {
		return makeRPCOutput(EMPTY_HASH, auction.ErrInvalidTargetTxHash)
	}
	errTargetTxSend := api.a.Backend.SendTx(ctx, targetTx)
	// ignore known transaction related errors against target tx validation
	if errTargetTxSend != nil && !(strings.HasPrefix(errTargetTxSend.Error(), "known transaction:") || errors.Is(errTargetTxSend, gasless_impl.ErrUnableToAddKnownBundleTx)) {
		return makeRPCOutput(EMPTY_HASH, errTargetTxSend)
	}

	// 2. add bid
	bid := ToBid(bidInput)
	bidHash, errValidateBid := api.a.bidPool.AddBid(bid)
	return makeRPCOutput(bidHash, errValidateBid)
}
```

**File:** kaiax/auction/impl/bid_pool.go (L38-48)
```go
const (
	bidChSize        = 2048
	allowFutureBlock = 2

	BidTxMaxCallGasLimit = uint64(10_000_000)
	BidTxMaxDataSize     = uint64(64 * 1024) // 64KB

	// Rate limiting
	bidsPerSecondPerPeer = 300 // Max bids per second per peer
	rateLimiterCacheSize = 1024
)
```

**File:** kaiax/auction/impl/bid_pool.go (L294-309)
```go
	// If same block number, same target tx hash exists, replace it if it's better
	if existingBid, ok := bp.bidTargetMap[blockNumber][targetTxHash]; ok {
		// FCFS if the bid is the same.
		if existingBid.Bid.Cmp(bid.Bid) >= 0 {
			return auction.ErrLowBid
		}

		logger.Trace("Replace bid", "old", existingBid.Hash(), "new", bid.Hash())
		delete(bp.bidMap, existingBid.Hash())
		delete(bp.bidWinnerMap[blockNumber], existingBid.Sender)
	} else {
		if int64(len(bp.bidMap)) >= bp.maxBidPoolSize {
			logger.Info("Bid pool is full", "maxBidPoolSize", bp.maxBidPoolSize, "bid", bid.Hash())
			return auction.ErrBidPoolFull
		}
	}
```

**File:** kaiax/auction/config.go (L28-31)
```go
const (
	DefaultMaxBidPoolSize = math.MaxInt64
	DefaultEDOffset       = 200 * time.Millisecond
)
```

**File:** kaiax/auction/config.go (L69-75)
```go
func DefaultAuctionConfig() *AuctionConfig {
	return &AuctionConfig{
		Disable:        false,
		MaxBidPoolSize: DefaultMaxBidPoolSize,
		EDOffset:       DefaultEDOffset,
	}
}
```

**File:** kaiax/auction/config.go (L77-85)
```go
func SetAuctionConfig(ctx *cli.Context, cfg *AuctionConfig, nodeType common.ConnType) {
	disable := ctx.Bool(DisableFlag.Name)
	// Disable auction module for non-consensus nodes
	if nodeType != common.CONSENSUSNODE {
		disable = true
	}

	cfg.Disable = disable
	cfg.MaxBidPoolSize = DefaultMaxBidPoolSize
```
