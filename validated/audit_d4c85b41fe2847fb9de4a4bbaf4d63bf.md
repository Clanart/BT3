### Title
Auction module bid-size and gas-limit bounds are hardcoded Go constants, unlike KIP-71's mutable governance bounds, causing the auction module to become permanently inoperable if network conditions change - (File: `kaiax/auction/impl/bid_pool.go`)

### Summary
The `kaiax/auction` module rejects any bid whose `CallGasLimit` exceeds `BidTxMaxCallGasLimit` or whose `Data` exceeds `BidTxMaxDataSize`. Both values are hardcoded package-level Go constants rather than governance-mutable parameters, unlike the analogous KIP-71 `LowerBoundBaseFee`/`UpperBoundBaseFee` values which are proper `kaiax/gov` parameters that can be updated on-chain by the governing node without a binary upgrade.

### Finding Description
`BidTxMaxCallGasLimit` and `BidTxMaxDataSize` are defined as fixed constants: [1](#0-0) 

These constants directly gate whether a submitted bid is admitted to the bid pool in `validateBid`: [2](#0-1) 

Contrast this with `AuctionConfig`, which only exposes `Disable`, `MaxBidPoolSize`, and `EDOffset` as configurable (CLI-flag driven, node-local) knobs — `BidTxMaxCallGasLimit`/`BidTxMaxDataSize` are absent from it entirely: [3](#0-2) 

This is structurally identical to the reported `ClearingHouse` bug class: the KIP-71 base-fee bounds in this codebase were correctly made governance-mutable (`Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee` are registered `gov.Param`s with `ChainConfigValue` accessors and vote-consistency checks), so the network can adapt them as market/network conditions evolve: [4](#0-3) [5](#0-4) 

The auction module's analogous bounds received no such treatment: `BidTxMaxCallGasLimit`/`BidTxMaxDataSize` cannot be adjusted by governance vote, node operator config, or any runtime mechanism — only a source-code change and a coordinated binary release/hardfork can move them.

### Impact Explanation
If real-world conditions change such that these fixed values become inappropriate (e.g., EVM gas repricing that makes `10_000_000` gas insufficient for legitimate searcher call patterns, or calldata-heavy MEV strategies that need more than 64KB), every bid exceeding the bound is unconditionally rejected with `ErrExceedMaxCallGasLimit`/`ErrExceedMaxDataSize`, regardless of the `Auctioneer`'s own signed approval. This makes the entire KIP-249 auction/MEV-capture mechanism permanently inoperable for a class of legitimate transactions network-wide, with no governance-level remediation path — the only fix requires a coordinated software upgrade across all consensus nodes, unlike KIP-71 params which self-heal via a governance vote.

### Likelihood Explanation
This is not an active-exploit scenario but a design/availability defect: it manifests whenever real market conditions for auction bids diverge from the values chosen at compile time. Given that gas costs and MEV strategies evolve, and the module explicitly documents these as protocol-level bid-pool validation rules that searchers/auctioneers must satisfy, the likelihood of these fixed bounds becoming a practical bottleneck over time is significant, mirroring the exact rationale in the original report.

### Recommendation
Migrate `BidTxMaxCallGasLimit` and `BidTxMaxDataSize` into the `kaiax/gov` parameter set (or at minimum into `AuctionConfig`/`SystemRegistry`-controlled on-chain configuration) so they can be updated by governance vote as network conditions change, consistent with how KIP-71's base-fee bounds are already handled.

### Proof of Concept
1. Observe the auction bid-pool constants are compile-time fixed: [1](#0-0) 
2. Observe `validateBid` rejects any bid exceeding these fixed values unconditionally: [2](#0-1) 
3. Observe that `AuctionConfig`, the only externally configurable structure for this module, does not expose these fields: [3](#0-2) 
4. Compare with `kaiax/gov`'s `Kip71LowerBoundBaseFee`/`Kip71UpperBoundBaseFee`, which are properly registered as mutable governance parameters with format/consistency checks: [4](#0-3) 
5. Existing tests confirm the hard rejection behavior once thresholds are crossed, with no override mechanism available: [6](#0-5)

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L38-44)
```go
const (
	bidChSize        = 2048
	allowFutureBlock = 2

	BidTxMaxCallGasLimit = uint64(10_000_000)
	BidTxMaxDataSize     = uint64(64 * 1024) // 64KB

```

**File:** kaiax/auction/impl/bid_pool.go (L379-387)
```go
	// 4. The data size must be less than the maximum limit.
	if uint64(len(bid.Data)) > BidTxMaxDataSize {
		return auction.ErrExceedMaxDataSize
	}

	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
	}
```

**File:** kaiax/auction/config.go (L63-67)
```go
type AuctionConfig struct {
	Disable        bool
	MaxBidPoolSize int64
	EDOffset       time.Duration
}
```

**File:** kaiax/gov/param.go (L335-367)
```go
	Kip71LowerBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.LowerBoundBaseFee, nil
		},
		DefaultValue: uint64(25000000000),
	},
	Kip71MaxBlockGasUsedForBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.MaxBlockGasUsedForBaseFee, nil
		},
		DefaultValue: uint64(60000000),
	},
	Kip71UpperBoundBaseFee: {
		Canonicalizer: uint64Canonicalizer,
		FormatChecker: noopFormatChecker,
		ChainConfigValue: func(c *params.ChainConfig) (any, error) {
			if c.Governance == nil || c.Governance.KIP71 == nil {
				return nil, errors.New("kip71 is not set")
			}
			return c.Governance.KIP71.UpperBoundBaseFee, nil
		},
		DefaultValue: uint64(750000000000),
	},
```

**File:** kaiax/gov/headergov/impl/header.go (L188-201)
```go
	case gov.Kip71LowerBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) > params.UpperBoundBaseFee {
			return ErrLowerBoundBaseFee
		} else {
			return nil
		}
	case gov.Kip71UpperBoundBaseFee:
		params := h.GetParamSet(blockNum)
		if vote.Value().(uint64) < params.LowerBoundBaseFee {
			return ErrUpperBoundBaseFee
		} else {
			return nil
		}
```

**File:** kaiax/auction/impl/bid_pool_test.go (L266-291)
```go
func TestBidPool_AddBid_ExceedMaxDataSize(t *testing.T) {
	var (
		mockCtrl = gomock.NewController(t)
		chain    = chain_mock.NewMockBlockChain(mockCtrl)
		block1   = types.NewBlockWithHeader(&types.Header{Number: big.NewInt(1)})
	)
	defer mockCtrl.Finish()

	pool := NewBidPool(testChainConfig, chain, &auction.AuctionConfig{MaxBidPoolSize: 1024})
	require.NotNil(t, pool)

	chain.EXPECT().CurrentBlock().Return(block1).Times(1)

	// Start the auction
	pool.start()
	atomic.StoreUint32(&pool.running, 1)
	defer pool.stop()
	pool.auctioneer = testAuctioneer
	pool.auctionEntryPoint = testAuctionEntryPoint
	pool.auctionEntryPointVersion = auction.AuctionVersionV2

	// Test bid with data size exceed max data size
	bid := testBids[6]
	_, err := pool.AddBid(bid)
	assert.Equal(t, auction.ErrExceedMaxDataSize, err)
}
```
