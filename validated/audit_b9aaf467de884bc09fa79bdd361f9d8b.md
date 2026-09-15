### Title
Auction BidTx generator ignores `bid.MaxGasPrice`, allowing bidders to be overcharged when block base fee spikes after bid signing - ([File: kaiax/auction/impl/getter.go])

### Summary
The Tidal `Pool.buy` bug class (price parameter can drift upward between when a user commits to a price and when the transaction actually executes, causing the user to pay more than intended, with no on-chain cap enforced at execution time) has a structural analog in Kaia's KIP-249 auction module. A searcher's signed `Bid` includes a `MaxGasPrice` field that is meant to cap how much gas-fee exposure the bidder accepts for the auction `call()` to be executed by the block proposer, but the node-side code that actually builds the `BidTx` transaction never reads or enforces `bid.MaxGasPrice` — it instead just copies the fee cap fields from the unrelated target transaction.

### Finding Description
`auction.BidData` includes an explicit, searcher-signed `MaxGasPrice` field intended to bound gas-price exposure for the auction call: [1](#0-0) 

When the v3 auction ABI is used, `MaxGasPrice` is faithfully encoded into the on-chain calldata sent to the `AuctionEntryPoint` contract: [2](#0-1) 

However, the node-local `BidPool.validateBid` — which is the only bid-admission gate before a bid is accepted and queued for BidTx generation — never checks `bid.MaxGasPrice` against anything (it only checks sender/winner state, block-number range, `Bid>0`, data size, call-gas-limit, and signatures): [3](#0-2) 

More importantly, the function that actually constructs the `BidTx` to be broadcast/included, `GetBidTxGenerator`, sets the transaction's `GasFeeCap`/`GasTipCap` directly from the **target transaction** (`tx.GasFeeCap()`, `tx.GasTipCap()`), completely independent of `bid.MaxGasPrice`: [4](#0-3) 

This is confirmed by the corresponding test, which asserts the generated BidTx inherits its fee caps from the target tx, not from the bid: [5](#0-4) 

Because the block's base fee (KIP-71/Magma dynamic base fee) can increase between the time a searcher signs and submits a bid (with an implicit expectation, expressed via `MaxGasPrice`, of the worst-case gas cost they will bear through the auction settlement) and the time the block containing the target tx and BidTx is actually built, an intervening state change (e.g., a burst of gas usage in the mempool that pushes the base fee up, analogous to the front-running `weeklyPremium` update in the Tidal report) can cause the BidTx's actual gas price/fee exposure to exceed what the bidder signed `MaxGasPrice` for. Because the node client never compares the generated BidTx's effective gas price to `bid.MaxGasPrice` before submission, and never reverts or aborts bid execution locally when this cap would be exceeded, the protection the field is meant to provide is not enforced at the node/BidPool layer — this mirrors exactly the missing-guardrail pattern the Tidal report flagged (no check that `allPremium`/actual cost ≤ user-approved maximum before committing funds).

### Impact Explanation
If the on-chain `AuctionEntryPoint` (v3) contract does not independently and correctly enforce `maxGasPrice` against the real gas price at execution (the contract's Solidity source is not present in this repository to verify; only bindings/mocks are indexed), a bidder's signed maximum-gas-price guardrail can be silently bypassed by the node's BidTx construction logic, resulting in the bidder being charged (via bid/gas-refund accounting) more than the maximum they explicitly authorized. This is a state-divergence/fee-abuse risk affecting fund settlement correctness for every auction participant (searcher/bidder), a class of unprivileged, permissionless transaction senders reachable purely by submitting a bid via `auction_submitBid`.

### Likelihood Explanation
Medium: `MaxGasPrice` is a legitimate signed guardrail field that any bidder (unprivileged RPC caller) can and would rely on, and the node code path that constructs the executable BidTx (`GetBidTxGenerator`) demonstrably never reads it. The only mitigating factor is that final enforcement may occur inside the deployed `AuctionEntryPoint` contract itself (not present in this codebase to verify), so full exploitability depends on that contract's `maxGasPrice` check, if any, at settlement time.

### Recommendation
In `kaiax/auction/impl/getter.go` (`GetBidTxGenerator`) and/or `kaiax/auction/impl/bid_pool.go` (`validateBid`), explicitly validate that the derived `GasFeeCap`/effective gas price of the generated BidTx does not exceed `bid.MaxGasPrice` when the field is set, and reject/drop the bid (mirroring the Tidal recommendation of reverting when the actual cost exceeds a caller-specified maximum) rather than silently inheriting fee-cap parameters from the unrelated target transaction. This should be paired with a review of the on-chain `AuctionEntryPoint.call()` implementation to confirm it independently reverts when `tx.gasprice > auctionTx.maxGasPrice`.

### Proof of Concept
1. Searcher constructs and EIP-712-signs a `Bid` with `MaxGasPrice = X`, targeting a pending transaction `T`, and submits it via `auction_submitBid`.
2. `BidPool.validateBid` accepts the bid without checking `MaxGasPrice` at all: [3](#0-2) 
3. Before the block containing `T` is built, network activity or a competing transaction increases the KIP-71 base fee (per `NextMagmaBlockBaseFee`): [6](#0-5) 
4. `GetBidTxGenerator` builds the BidTx using `tx.GasFeeCap()`/`tx.GasTipCap()` (from target tx `T`), not `bid.MaxGasPrice`: [7](#0-6) 
5. The BidTx is included with an effective gas price/gas cost above what the bidder specified as their maximum via `MaxGasPrice`, with no client-side check preventing this — verifying whether the bidder is actually protected requires inspecting the deployed `AuctionEntryPoint` v3 contract's `call()` logic, which is outside this repository's indexed contents.

### Citations

**File:** kaiax/auction/bid.go (L32-44)
```go
type BidData struct {
	TargetTxHash  common.Hash    `json:"targetTxHash"`
	BlockNumber   uint64         `json:"blockNumber"`
	Sender        common.Address `json:"sender"`
	To            common.Address `json:"to"`
	Nonce         uint64         `json:"nonce"`
	Bid           *big.Int       `json:"bid"`
	MaxGasPrice   *big.Int       `json:"maxGasPrice,omitempty"`
	CallGasLimit  uint64         `json:"callGasLimit"`
	Data          []byte         `json:"data"`
	SearcherSig   []byte         `json:"searcherSig"`
	AuctioneerSig []byte         `json:"auctioneerSig"`
}
```

**File:** blockchain/system/auction.go (L83-103)
```go
func EncodeAuctionCallData(bid *auction.Bid, version string) ([]byte, error) {
	if version == auction.AuctionVersionV3 {
		maxGasPrice := bid.MaxGasPrice
		if maxGasPrice == nil {
			maxGasPrice = new(big.Int)
		}
		input := contractsv3.IAuctionEntryPointAuctionTx{
			TargetTxHash:  bid.TargetTxHash,
			BlockNumber:   new(big.Int).SetUint64(bid.BlockNumber),
			Sender:        bid.Sender,
			To:            bid.To,
			Nonce:         new(big.Int).SetUint64(bid.Nonce),
			Bid:           bid.Bid,
			MaxGasPrice:   maxGasPrice,
			CallGasLimit:  new(big.Int).SetUint64(bid.CallGasLimit),
			Data:          bid.Data,
			SearcherSig:   bid.SearcherSig,
			AuctioneerSig: bid.AuctioneerSig,
		}
		return abiV3.Pack("call", input)
	}
```

**File:** kaiax/auction/impl/bid_pool.go (L345-395)
```go
func (bp *BidPool) validateBid(bid *auction.Bid) error {
	blockNumber := bid.BlockNumber

	bp.bidMu.RLock()

	// Check if the auction tx is already in the pool.
	if _, ok := bp.bidMap[bid.Hash()]; ok {
		bp.bidMu.RUnlock()
		return auction.ErrBidAlreadyExists
	}

	// 1. The `bid.Sender` must not be in the winner list of the same block number if the new bid isn't equal to the previous bid.
	if bp.senderHasDifferentWinner(bid) {
		bp.bidMu.RUnlock()
		return auction.ErrBidSenderExists
	}
	bp.bidMu.RUnlock()

	curBlock := bp.Chain.CurrentBlock()
	if curBlock == nil {
		return auction.ErrBlockNotFound
	}

	// 2. The `bid.BlockNumber` must be in range of `[currentBlockNumber + 1, currentBlockNumber + allowFutureBlock]`.
	curNum := curBlock.NumberU64()
	if blockNumber <= curNum || blockNumber > curNum+allowFutureBlock {
		return auction.ErrInvalidBlockNumber
	}

	// 3. The `bid.Bid` must be greater than 0.
	if bid.Bid.Sign() <= 0 {
		return auction.ErrZeroBid
	}

	// 4. The data size must be less than the maximum limit.
	if uint64(len(bid.Data)) > BidTxMaxDataSize {
		return auction.ErrExceedMaxDataSize
	}

	// 5. The gas limit must be less than the maximum limit.
	if bid.CallGasLimit > BidTxMaxCallGasLimit {
		return auction.ErrExceedMaxCallGasLimit
	}

	// 6. The `bid.SearcherSig` and `bid.AuctioneerSig` must be valid.
	if err := bp.validateBidSigs(bid); err != nil {
		return err
	}

	return nil
}
```

**File:** kaiax/auction/impl/getter.go (L27-67)
```go
func (a *AuctionModule) GetBidTxGenerator(tx *types.Transaction, bid *auction.Bid) *builder.TxOrGen {
	gen := func(nonce uint64) (*types.Transaction, error) {
		var (
			chainId           = a.InitOpts.ChainConfig.ChainID
			signer            = types.LatestSignerForChainID(chainId)
			auctionEntryPoint = a.bidPool.GetAuctionEntryPoint()
			key               = a.InitOpts.NodeKey
		)

		data, err := system.EncodeAuctionCallData(bid, a.bidPool.GetAuctionEntryPointVersion())
		if err != nil {
			return nil, err
		}

		if bid.GetGasLimit() == 0 {
			gasLimit, err := a.bidPool.getBidTxGasLimit(bid)
			if err != nil {
				return nil, err
			}
			bid.SetGasLimit(gasLimit)
		}

		tx, err := types.NewTransactionWithMap(types.TxTypeEthereumDynamicFee, map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      nonce,
			types.TxValueKeyTo:         &auctionEntryPoint,
			types.TxValueKeyAmount:     common.Big0,
			types.TxValueKeyData:       data,
			types.TxValueKeyGasLimit:   bid.GetGasLimit(),
			types.TxValueKeyGasFeeCap:  tx.GasFeeCap(),
			types.TxValueKeyGasTipCap:  tx.GasTipCap(),
			types.TxValueKeyAccessList: types.AccessList{},
			types.TxValueKeyChainID:    chainId,
		})
		if err != nil {
			return nil, err
		}

		err = tx.Sign(signer, key)

		return tx, err
	}
```

**File:** kaiax/auction/impl/getter_test.go (L45-82)
```go
func TestGetBidTxGenerator(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlWarn)
	module := prep(t)

	module.Start()
	defer module.Stop()

	// Arbitrary target tx
	tx := types.NewTransaction(0, common.HexToAddress("0x5FC8d32690cc91D4c39d9d3abcBD16989F875701"), big.NewInt(0), 1000000, big.NewInt(100), []byte("d09de08a"))

	bid := genBid(tx.Hash())
	txOrGen := module.GetBidTxGenerator(tx, bid)
	require.NotNil(t, txOrGen)

	gasLimit, err := module.bidPool.getBidTxGasLimit(bid)
	require.NoError(t, err)

	// Generate transaction from the generator function
	generatedTx, err := txOrGen.GetTx(0)
	require.NoError(t, err)
	require.NotNil(t, generatedTx)

	// Verify transaction properties
	require.Equal(t, uint16(generatedTx.Type()), uint16(0x7802))
	require.Equal(t, uint64(0), generatedTx.Nonce())
	require.Equal(t, module.bidPool.auctionEntryPoint, *generatedTx.To())
	require.Equal(t, common.Big0, generatedTx.Value())
	require.Equal(t, gasLimit, generatedTx.Gas())
	require.Equal(t, tx.GasFeeCap(), generatedTx.GasFeeCap())
	require.Equal(t, tx.GasTipCap(), generatedTx.GasTipCap())
	require.Equal(t, module.bidPool.ChainConfig.ChainID, generatedTx.ChainId())

	// Verify the transaction is properly signed by the auctioneer
	signer := types.LatestSignerForChainID(module.bidPool.ChainConfig.ChainID)
	sender, err := signer.Sender(generatedTx)
	require.NoError(t, err)
	require.Equal(t, crypto.PubkeyToAddress(module.InitOpts.NodeKey.PublicKey), sender)
}
```

**File:** params/kip71_config.go (L58-129)
```go
func (kc *KIP71Config) NextMagmaBlockBaseFee(parentHeaderNumber *big.Int, parentHeaderBaseFee *big.Int, parentHeaderGasUsed uint64) *big.Int {
	// governance parameters
	lowerBoundBaseFee := new(big.Int).SetUint64(kc.LowerBoundBaseFee)
	upperBoundBaseFee := new(big.Int).SetUint64(kc.UpperBoundBaseFee)
	makeEvenByCeil(lowerBoundBaseFee)
	makeEvenByFloor(upperBoundBaseFee)

	// If the parent is the magma disabled block or genesis, then return the lowerBoundBaseFee (default 25ston)
	if parentHeaderNumber.Cmp(new(big.Int).SetUint64(0)) == 0 || parentHeaderBaseFee == nil {
		return makeEvenByFloor(lowerBoundBaseFee)
	}

	var baseFeeDenominator *big.Int
	if kc.BaseFeeDenominator == 0 {
		// To avoid panic, set the fluctuation range small
		baseFeeDenominator = new(big.Int).SetUint64(64)
	} else {
		baseFeeDenominator = new(big.Int).SetUint64(kc.BaseFeeDenominator)
	}
	gasTarget := kc.GasTarget
	upperGasLimit := kc.MaxBlockGasUsedForBaseFee

	// check the case of upper/lowerBoundBaseFee is updated by governance mechanism
	parentBaseFee := parentHeaderBaseFee
	if parentBaseFee.Cmp(upperBoundBaseFee) >= 0 {
		parentBaseFee = upperBoundBaseFee
	} else if parentBaseFee.Cmp(lowerBoundBaseFee) <= 0 {
		parentBaseFee = lowerBoundBaseFee
	}

	// upper gas limit cut off the impulse of used gas to upper bound
	parentGasUsed := min(parentHeaderGasUsed, upperGasLimit)
	if parentGasUsed == gasTarget {
		return makeEvenByFloor(parentBaseFee)
	} else if parentGasUsed > gasTarget {
		// shortcut. If parentBaseFee is already reached upperbound, do not calculate.
		if parentBaseFee.Cmp(upperBoundBaseFee) == 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		// If the parent block used more gas than its target,
		// the baseFee of the next block should increase.
		// baseFeeDelta = max(1, parentBaseFee * (parentGasUsed - gasTarget) / gasTarget / baseFeeDenominator)
		gasUsedDelta := new(big.Int).SetUint64(parentGasUsed - gasTarget)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := math.BigMax(x.Div(y, baseFeeDenominator), common.Big1)

		nextBaseFee := x.Add(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(upperBoundBaseFee) > 0 {
			return makeEvenByFloor(upperBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	} else {
		// shortcut. If parentBaseFee is already reached lower bound, do not calculate.
		if parentBaseFee.Cmp(lowerBoundBaseFee) == 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		// Otherwise if the parent block used less gas than its target,
		// the baseFee of the next block should decrease.
		// baseFeeDelta = parentBaseFee * (gasTarget - parentGasUsed) / gasTarget / baseFeeDenominator
		gasUsedDelta := new(big.Int).SetUint64(gasTarget - parentGasUsed)
		x := new(big.Int).Mul(parentBaseFee, gasUsedDelta)
		y := x.Div(x, new(big.Int).SetUint64(gasTarget))
		baseFeeDelta := x.Div(y, baseFeeDenominator)

		nextBaseFee := x.Sub(parentBaseFee, baseFeeDelta)
		if nextBaseFee.Cmp(lowerBoundBaseFee) < 0 {
			return makeEvenByFloor(lowerBoundBaseFee)
		}
		return makeEvenByFloor(nextBaseFee)
	}
}
```
