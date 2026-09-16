### Title
Incorrect gas-limit formula in auction bid-tx sizing underestimates required gas, risking bid-tx revert / auction settlement DoS - ([File: kaiax/auction/impl/bid_pool.go])

### Summary
The auction bid gas-limit calculation in `getBidTxGasLimit` combines the EIP-7623 floor-data-gas check with the intrinsic-gas/call-gas/buffer sum using `max()` in a way that mirrors the "formula uses the wrong/mismatched quantity" class of bug from the referenced zkSync report: the two terms being `max`-ed are not commensurate, so when the floor-data-gas term dominates, the buffer and searcher-supplied `CallGasLimit` are silently dropped from the resulting gas limit.

### Finding Description
`getBidTxGasLimit` computes the gas limit to assign to a proposer-submitted auction transaction as: [1](#0-0) 

```go
func (bp *BidPool) getBidTxGasLimit(bid *auction.Bid) (uint64, error) {
	...
	intrinsicGas, err := types.IntrinsicGas(data, nil, nil, false, rules)
	...
	floorDataGas := uint64(0)
	if rules.IsPrague {
		floorDataGas, err = blockchain.FloorDataGas(types.TxTypeEthereumDynamicFee, data, 0)
		...
	}

	return max(intrinsicGas+bid.CallGasLimit+buffer, floorDataGas), nil
}
```

`FloorDataGas` (EIP‑7623) returns the *minimum total gas* the transaction must have (`txGas + tokens*TxCostFloorPerToken + sigValidateGas`), i.e. a lower bound that is supposed to be compared against — and dominate over — the "normal" gas accounting (`intrinsicGas + payload/execution gas`) only when the floor is larger than the ordinary computation: [2](#0-1) 

The correct pattern, as used elsewhere in the transaction-pool validation path, is to take the intrinsic/floor gas as the *base cost* and then add the caller-supplied execution budget (`bid.CallGasLimit`) and the auctioneer's estimation buffer on top — i.e. `max(intrinsicGas, floorDataGas) + bid.CallGasLimit + buffer`. Compare with `blockchain/tx_pool.go`, where floor gas is checked as an independent minimum against the full `tx.Gas()`, not folded into a max against a partial sum: [3](#0-2) 

Instead, `getBidTxGasLimit` puts `intrinsicGas + bid.CallGasLimit + buffer` on one side of `max` and the bare `floorDataGas` (which does not include `CallGasLimit` or `buffer`) on the other. Whenever a bid's payload is large enough that `floorDataGas > intrinsicGas + CallGasLimit + buffer` (e.g., a call-data-heavy bid with a comparatively small `CallGasLimit`), the function returns `floorDataGas` alone, entirely dropping the searcher's requested `CallGasLimit` and the auctioneer's `buffer`. This is structurally the same class of defect as the audit finding: two different formula components (`Tm`/`Pm` analog here being "floor gas" vs. "intrinsic+call+buffer gas") are conflated via a single `max()`/formula without accounting for what each component is supposed to represent, so the assembled gas limit silently loses required units of gas.

### Impact Explanation
The gas limit produced by `getBidTxGasLimit` is set on the auction settlement transaction that the auctioneer/proposer path builds to execute a searcher's winning bid via the `AuctionEntryPoint` contract (`bid.SetGasLimit(gasLimit)` at [4](#0-3) ). If the assigned gas limit is underestimated because `CallGasLimit`/`buffer` are dropped, the resulting on-chain call can run out of gas mid-execution and revert despite the bid/searcher intending to supply enough gas for `CallGasLimit`. This can cause legitimate winning bids to fail during block assembly/settlement (denial of service for the bidder/searcher, and a failed or unfair auction outcome), which maps to "acceptance of an invalid/incorrectly-priced transaction" and "gasless/auction settlement" failure categories called out as in-scope impact classes.

### Likelihood Explanation
This path is reachable by any unprivileged auction bidder/searcher: `Bid.CallGasLimit` and the size of the ABI-encoded call `data` are attacker-controlled inputs submitted via the public bid-submission RPC/gossip path, which flows into `AddBid` → `getBidTxGasLimit` [5](#0-4) . A searcher only needs to craft a bid whose encoded call data is large enough (bounded by `BidTxMaxDataSize = 64KB`, [6](#0-5) ) relative to a modest `CallGasLimit` to trigger the floor-gas branch dominating. Because EIP‑7623 floor gas scales with data size (`tokens*TxCostFloorPerToken`), a bid with tens of KB of calldata and a small `CallGasLimit` can plausibly exceed `intrinsicGas + CallGasLimit + buffer`, making this a low-effort, unprivileged, deterministic trigger — no special permissions or governance changes are needed.

### Recommendation
Change the formula to take the maximum of the two "floor" quantities first and then add the execution budget and buffer on top:
```go
return max(intrinsicGas, floorDataGas) + bid.CallGasLimit + buffer, nil
```
Add unit tests (mirroring `TestBidPool_AddBid_ExceedMaxGasLimit`) with large-calldata / small-`CallGasLimit` bids to assert the returned gas limit always includes the full `CallGasLimit + buffer`, and document in-code the intended relationship between `intrinsicGas`, `floorDataGas` (EIP‑7623), `bid.CallGasLimit`, and `buffer`, analogous to the recommendation in the referenced report to document formula/constant correspondence directly in code.

### Proof of Concept
1. Craft a bid whose `data` (ABI-encoded auction call payload via `system.EncodeAuctionCallData`) is large, e.g. tens of KB (within `BidTxMaxDataSize`), consisting mostly of non-zero bytes to maximize `FloorDataGas`'s `tokens*TxCostFloorPerToken` term.
2. Set `bid.CallGasLimit` to a small value (e.g., a few hundred thousand) representing the actual gas the target call needs.
3. Submit the bid via the public bid RPC/gossip path so it reaches `BidPool.AddBid` → `getBidTxGasLimit`.
4. Because `floorDataGas` (which excludes `CallGasLimit` and `buffer`) exceeds `intrinsicGas + CallGasLimit + buffer`, `max(...)` returns `floorDataGas`, i.e., the settlement transaction is assigned a gas limit lower than `floorDataGas + CallGasLimit` would require for the actual call execution to succeed.
5. When the proposer executes the resulting transaction against the `AuctionEntryPoint` contract, execution can run out of gas partway through the call (`CallGasLimit` worth of execution is not actually available), causing the settlement to revert or fail despite the bid being well-formed and adequately funded — denying the winning searcher's execution.

### Citations

**File:** kaiax/auction/impl/bid_pool.go (L42-43)
```go
	BidTxMaxCallGasLimit = uint64(10_000_000)
	BidTxMaxDataSize     = uint64(64 * 1024) // 64KB
```

**File:** kaiax/auction/impl/bid_pool.go (L252-269)
```go
func (bp *BidPool) AddBid(bid *auction.Bid) (common.Hash, error) {
	if atomic.LoadUint32(&bp.running) == 0 {
		return common.Hash{}, auction.ErrAuctionPaused
	}

	if err := bp.validateBid(bid); err != nil {
		return common.Hash{}, err
	}

	if err := bp.insertBid(bid); err != nil {
		return common.Hash{}, err
	}

	gasLimit, err := bp.getBidTxGasLimit(bid)
	if err != nil {
		return common.Hash{}, err
	}
	bid.SetGasLimit(gasLimit)
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

**File:** blockchain/state_transition.go (L840-860)
```go
// FloorDataGas calculates the minimum gas required for a transaction
// based on its data tokens (EIP-7623).
func FloorDataGas(txType types.TxType, data []byte, sigValidateGas uint64) (uint64, error) {
	var (
		z      = uint64(bytes.Count(data, []byte{0}))
		nz     = uint64(len(data)) - z
		tokens = nz*params.TxTokenPerNonZeroByte + z
	)
	// Check for overflow
	// Instead of using parmas.TxGas, we should consider the tx type
	// because Kaia tx type has different tx gas (e.g., fee delegated tx).
	txGas, err := types.GetTxGasForTxType(txType)
	if err != nil {
		return 0, err
	}
	if (math.MaxUint64-txGas-sigValidateGas)/params.TxCostFloorPerToken < tokens {
		return 0, types.ErrGasUintOverflow
	}
	// We add up sig validate gas too, as it's the final floor gas
	return txGas + tokens*params.TxCostFloorPerToken + sigValidateGas, nil
}
```

**File:** blockchain/tx_pool.go (L991-1008)
```go
	intrGas, err := tx.IntrinsicGas(pool.currentBlockNumber)
	sigValGas := gasFrom + gasFeePayer
	if err != nil {
		return err
	}
	if tx.Gas() < intrGas+sigValGas {
		return ErrIntrinsicGas
	}
	// Ensure the transaction can cover floor data gas.
	if pool.rules.IsPrague {
		floorDataGas, err := FloorDataGas(tx.Type(), tx.Data(), sigValGas)
		if err != nil {
			return err
		}
		if tx.Gas() < floorDataGas {
			return fmt.Errorf("%w: gas %v, minimum needed %v", ErrFloorDataGas, tx.Gas(), floorDataGas)
		}
	}
```
