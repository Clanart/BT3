## Analog Found

### Title
Auction bids can be griefed with a minimal 1-wei bid while the CN-funded `BidTx` gas cost is fully attacker-controlled, making it a guaranteed loss to execute - (File: `kaiax/auction/impl/bid_pool.go`, `kaiax/auction/impl/getter.go`)

### Summary
The original report describes an order/escrow system where a counterparty can leave a trade at a dust size (1 wei) so that closing it costs the operator more in transaction fees than the fees earned, making it -EV for the operator to settle. The Kaia auction module (KIP-249) has the same class of flaw: `BidPool.validateBid` only requires `bid.Bid > 0` with no floor relative to the gas cost the CN itself must pay to submit the corresponding `BidTx`, and that gas cost is derived from parameters (`GasFeeCap`/`GasTipCap`) taken directly from the attacker-controlled target transaction.

### Finding Description
`BidPool.validateBid` enforces only that the bid is nonzero: [1](#0-0) 

There is no check that `bid.Bid` covers (or even approaches) the gas cost of the `BidTx` that the node itself must generate and pay for out of its own key's balance, as documented: [2](#0-1) 

The `BidTx` generator sets the transaction's `GasFeeCap`/`GasTipCap` equal to the **target transaction's** fee cap/tip, i.e. values chosen by the (potentially unprivileged) searcher/bid sender who also crafts the target transaction, not by the CN: [3](#0-2) 

The gas limit for this self-funded `BidTx` is computed from intrinsic gas plus the bid's `CallGasLimit` (up to `BidTxMaxCallGasLimit = 10_000_000`) plus a buffer: [4](#0-3) 

Because block building unconditionally includes the bid bundle whenever a matching bid exists for an included target transaction, with no profitability check: [5](#0-4) 

an attacker who acts as (or colludes with) an auction bidder can submit a bid with `Bid = 1` wei alongside a target transaction that sets a high `GasFeeCap`/`GasTipCap` and a large `CallGasLimit`. The `Auctioneer` signature requirement gates *which* bids get accepted by the pool, but the on-chain/pool-level validation never checks that `bid.Bid` is worth the CN's own gas expenditure to execute — mirroring exactly the missing "minimum order size" check called out in the source report.

### Impact Explanation
Every time such a bid is honored, the CN funds and signs the `BidTx` from its own node key (documented as needing to hold `AuctionLenderMinBal`), paying real gas fees while receiving only 1 wei of bid revenue. Repeated or systematic submission of such bids drains value from the CN operator's key with each winning/executed bid, a direct, attacker-controlled economic loss to the block-producing party — analogous to the "operator will not be able to get paid" impact in the original report.

### Likelihood Explanation
Reachable purely through the public `auction_submitBid` RPC / bid gossip path by an "auction bidder" (an explicitly in-scope unprivileged actor), requiring only a valid searcher signature and a cooperating/careless auctioneer signature (or an auctioneer that does not itself enforce a minimum profitable bid, since the on-chain module does not). No consensus-level privilege is needed to construct the low bid; only the parameters of `validateBid` and `getBidTxGasLimit` govern acceptance.

### Recommendation
Enforce a minimum bid threshold in `BidPool.validateBid` (or in `AddBid`) that scales with the estimated `BidTx` execution cost — e.g., require `bid.Bid >= getBidTxGasLimit(bid) * bidGasPrice` (or a configurable minimum profit margin) before accepting the bid into the pool, rather than only checking `bid.Bid.Sign() <= 0`.

### Proof of Concept
1. Attacker crafts a target transaction with a high `GasFeeCap`/`GasTipCap` and includes a large `CallGasLimit` in the auction bid metadata.
2. Attacker (as searcher) signs a bid with `Bid = 1` (satisfies `bid.Bid.Sign() > 0` in `kaiax/auction/impl/bid_pool.go:375`).
3. Bid passes `validateBid` (block-number range, signature checks) and is inserted via `insertBid`.
4. On block building, `ExtractTxBundles` (`kaiax/auction/impl/builder.go:44-67`) unconditionally bundles the corresponding `BidTx`.
5. `GetBidTxGenerator` (`kaiax/auction/impl/getter.go:49-59`) builds and signs the `BidTx` using the CN's own key, with gas price copied from the attacker-chosen target tx and gas limit from `getBidTxGasLimit` (up to ~10M call gas plus buffer).
6. The CN pays the full gas cost for this transaction from its own balance while receiving 1 wei from the bid — a guaranteed net loss per occurrence, repeatable across blocks/targets.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L374-377)
```go
	// 3. The `bid.Bid` must be greater than 0.
	if bid.Bid.Sign() <= 0 {
		return auction.ErrZeroBid
	}
```

**File:** kaiax/auction/impl/bid_pool.go (L485-508)
```go
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
	bp.auctionInfoMu.RLock()
	buffer := bp.bidTxGasBuffer
	bp.auctionInfoMu.RUnlock()

	data, err := system.EncodeAuctionCallData(bid, bp.auctionEntryPointVersion)
	if err != nil {
		return 0, err
	}

	rules := bp.ChainConfig.Rules(big.NewInt(int64(bid.BlockNumber)))
	intrinsicGas, err := types.IntrinsicGas(data, nil, nil, false, rules)
	if err != nil {
		return 0, err
	}
	floorDataGas := uint64(0)
	if rules.IsPrague {
		floorDataGas, err = blockchain.FloorDataGas(types.TxTypeEthereumDynamicFee, data, 0)
		if err != nil {
			return 0, err
		}
	}

	return max(intrinsicGas+bid.CallGasLimit+buffer, floorDataGas), nil
```

**File:** kaiax/auction/README.md (L42-44)
```markdown
- Dependencies:
  - ChainConfig: To generate the latest signer.
  - NodeKey: For BidTxGenerator. The corresponding address should hold at least `AuctionLenderMinBal` of KAIA.
```

**File:** kaiax/auction/impl/getter.go (L49-59)
```go
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
```

**File:** kaiax/auction/impl/builder.go (L44-67)
```go
	for _, tx := range txs {
		txHash := tx.Hash()
		bid, ok := bidTargetMap[txHash]
		if !ok {
			continue
		}
		b := builder.NewBundle(
			builder.NewTxOrGenList(a.GetBidTxGenerator(tx, bid)),
			txHash,
			true,
		)

		isConflict := false
		for _, prev := range append(prevBundles, bundles...) {
			if prev.IsConflict(b) {
				isConflict = true
				break
			}
		}
		if isConflict {
			continue
		}
		bundles = append(bundles, b)
	}
```
