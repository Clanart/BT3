### Title
Auction searcher can bank the target-tx priority benefit while causing the settlement `BidTx` to fail, avoiding payment - (File: work/worker.go)

### Summary
In Kaia's KIP-249 auction module, a searcher's winning bid results in two *independent* transactions being placed in a block: the searcher's own **target transaction** (submitted directly to the pool via `SubmitBid`) and a separately generated **`BidTx`** that calls `AuctionEntryPoint.call()` to settle payment. These are not executed atomically as a single unit with the target tx — the block builder only requires that the target tx *precede* the bid tx and have succeeded; if the bid tx itself fails, only the bid tx is dropped, not the target tx that already captured the auction-provided ordering benefit.

### Finding Description
`AuctionAPI.SubmitBid` first sends the searcher's `targetTx` to the tx pool directly, and separately stores the bid in the bid pool: [1](#0-0) 

The block builder then builds a bundle containing only the `BidTx` (not the target tx) with `TargetTxHash` set to the target's hash, generated and signed by the node's own key against `AuctionEntryPoint.call(auctionTx)`: [2](#0-1) 

During block assembly, `shouldDiscardBundle` only checks that the immediately preceding executed transaction matches `TargetTxHash` and succeeded — it does not tie the target tx's inclusion to the bid tx's success: [3](#0-2) 

Crucially, in `ApplyTransactions`, the target transaction is committed to `env.txs`/`env.receipts` as an ordinary, already-finalized transaction *before* the bid bundle is attempted. When the bid bundle (`commitBundleTransaction`) subsequently fails inside the EVM (e.g., `AuctionEntryPoint.call()` reverts because the searcher's on-chain deposit/nonce state was drained or invalidated), only the bid tx's own effects are rolled back via `restoreEnv()`, and the failed bid tx is entirely excluded from the block (`kerrors.ErrRevertedBundleByVmErr` → `PopTxs`). The target tx, which already executed and captured the auction-granted priority position, remains committed in the block: [4](#0-3) [5](#0-4) 

The real `AuctionEntryPoint` contract (only interfaces/bindings are in-repo; the mock has the payment logic commented out) tracks payment via a **pre-funded deposit balance keyed by nonce** rather than value attached to the bid tx itself, as shown by `getNoncesAndDeposits(searchers)`: [6](#0-5) 

Because the contract exposes deposit/withdraw functionality (present in the ABI bindings) that the searcher (the deposit owner) can call, the searcher can submit an ordinary transaction that drains or invalidates their deposit/nonce state in the same block, timed so that it lands before the `BidTx` executes but does not disturb the already-independent target transaction. This causes `AuctionEntryPoint.call()` to revert for insufficient deposit (or nonce mismatch), and per the flow above, the bid tx is simply dropped from the block while the target tx — which already realized the auction-won ordering advantage (e.g., guaranteed placement immediately following a specific target) — stays included.

### Impact Explanation
This lets a searcher who wins an auction receive the full value of priority/ordering execution for their target transaction without paying the bid amount they committed to the auctioneer/proposer. This is a direct value-extraction/fee-delegation-style abuse: the auction's economic guarantee (payment for ordering privilege) is broken, and repeated exploitation would let searchers systematically avoid all auction payments while still consuming the scarce "immediately-after-target" execution slot, undermining the entire auction/MEV-redistribution mechanism (KIP-249) and depriving proposers/auctioneer of expected revenue.

### Likelihood Explanation
The searcher only needs to submit one additional, unprivileged transaction (a deposit withdrawal or nonce-invalidating action reachable by any depositor) in the same target block, which is well within reach of any address participating in the auction system — no special permissions, validator/consensus role, or node compromise required. The mechanics (target tx and bid tx as separate, non-atomically-linked transactions; the builder only excluding the failed bid tx and not the already-executed target tx) are structural to the current implementation of `ApplyTransactions`/`commitBundleTransaction`/`shouldDiscardBundle`.

### Recommendation
Enforce atomicity between the target transaction's economic benefit and bid settlement: either (a) require the bid tx and target tx to be bundled and rolled back together (target tx execution reverted/excluded if the bid tx fails), or (b) have `AuctionEntryPoint` collect/lock the bid amount at bid-submission time (escrow) rather than relying on a mutable, withdrawable deposit balance that the searcher can drain between bid submission and settlement in the same block. Additionally, consider locking/reserving the specific deposit amount used by an accepted bid so a subsequent withdrawal transaction from the same sender cannot invalidate it within the same block.

### Proof of Concept
1. Searcher deposits KAIA into `AuctionEntryPoint` and obtains a nonce/deposit balance sufficient to cover a bid.
2. Searcher calls `auction_submitBid` with a `targetTx` (their own transaction they want prioritized) and a signed bid; `SubmitBid` immediately broadcasts `targetTx` to the pool and the bid is accepted into the bid pool.
3. The `Auctioneer`/CN block-builder wins the auction and schedules `[targetTx, BidTx]` for the same block, per `GetBidTxGenerator`/`shouldDiscardBundle` logic [3](#0-2) .
4. Before the block is sealed, the searcher submits (from the same account) a normal transaction to `AuctionEntryPoint` that withdraws/reduces their deposit or otherwise invalidates the nonce the bid depends on, timed to land ahead of `BidTx` but not interfere with `targetTx` (a different tx entirely).
5. `targetTx` executes successfully and is committed to the block, capturing the priority-ordering benefit.
6. `BidTx` (`AuctionEntryPoint.call()`) reverts due to insufficient deposit/nonce mismatch; per `commitBundleTransaction`, only the bid tx's state changes are rolled back and the tx is popped from the block [7](#0-6) .
7. Final block contains `targetTx` (benefit realized) but not `BidTx` (no payment made) — the searcher obtained the auction-won ordering slot for free.

Note: the concrete `AuctionEntryPoint` Solidity source (deposit/withdraw implementation and exact nonce-invalidation semantics) is not present in this repository — only the interface/ABI bindings and a stripped-down mock (`AuctionEntryPointMock.sol`) with the payment/nonce logic commented out are available. Confirming the exact withdrawal/nonce-invalidation call and its precise revert conditions would require the full contract source, which is outside this repo's indexed content.

### Citations

**File:** kaiax/auction/impl/api.go (L124-141)
```go
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

**File:** work/worker.go (L783-815)
```go
		case blockchain.ErrTxTypeNotSupported:
			// Pop the unsupported transaction without shifting in the next from the account
			logger.Trace("Skipping unsupported transaction type", "sender", from, "type", tx.Type())
			builder.PopTxs(&incorporatedTxs, numShift, &bundles, env.signer)

		case kerrors.ErrRevertedBundleByVmErr:
			// Pop transaction in bundle reverted by vm err without shifting in the next from the account
			// During bundle execution, vm err is reverted, including the increment of the nonce, so a pop is executed.
			logger.Trace("Skipping transaction in bundle reverted by vm err", "sender", from, "hash", tx.Hash().String())
			builder.PopTxs(&incorporatedTxs, numShift, &bundles, env.signer)

		case kerrors.ErrTxGeneration:
			// Pop transaction in bundle due to tx generation error without shifting in the next from the account
			logger.Trace("Skipping transaction in bundle due to tx generation error", "err", err)
			builder.PopTxs(&incorporatedTxs, numShift, &bundles, env.signer)

		case nil:
			// Everything ok, collect the logs and shift in the next transaction from the same account
			coalescedLogs = append(coalescedLogs, logs...)
			builder.ShiftTxs(&incorporatedTxs, numShift)

		default:
			// Strange error, discard the transaction and get the next in line (note, the
			// nonce-too-high clause will prevent us from executing in vain).
			logger.Warn("Transaction failed, account skipped", "sender", from, "hash", tx.Hash().String(), "err", err)
			strangeErrorTxsCounter.Inc(1)
			builder.ShiftTxs(&incorporatedTxs, numShift)
		}
		if len(targetBundle.BundleTxs) != 0 {
			// After the last tx in the bundle finishes, set executingBundleTxs back to 0.
			isExecutingBundleTxs.Store(0)
		}
	}
```

**File:** work/worker.go (L877-954)
```go
func (env *Task) commitBundleTransaction(bundle *builder.Bundle, bc BlockChain, nodeAddr common.Address, vmConfig *vm.Config) (error, *types.Transaction, []*types.Log) {
	lastSnapshot := env.state.Copy()
	gasUsedSnapshot := env.header.GasUsed
	blobGasUsedSnapshot := env.header.BlobGasUsed
	blobsSnapshot := env.blobs
	tcountSnapshot := env.tcount
	txs := []*types.Transaction{}
	receipts := []*types.Receipt{}
	logs := []*types.Log{}

	markAllTxUnexecutable := func() {
		for _, txOrGen := range bundle.BundleTxs {
			if txOrGen.IsConcreteTx() {
				tx, _ := txOrGen.GetTx(0)
				tx.MarkUnexecutable(true)
			}
		}
	}

	restoreEnv := func() {
		env.state.Set(lastSnapshot)
		env.header.GasUsed = gasUsedSnapshot
		env.tcount = tcountSnapshot
		// blob related env are restored to the snapshot
		env.header.BlobGasUsed = blobGasUsedSnapshot
		env.blobs = blobsSnapshot
	}

	var totalTxSize uint64 = 0
	for _, txOrGen := range bundle.BundleTxs {
		tx, err := txOrGen.GetTx(env.state.GetNonce(nodeAddr))
		if err != nil {
			logger.Error("TxGenerator error", "error", err)
			markAllTxUnexecutable()
			restoreEnv()
			return kerrors.ErrTxGeneration, nil, nil
		}

		env.state.SetTxContext(tx.Hash(), common.Hash{}, env.tcount)
		receipt, _, err := bc.ApplyTransaction(env.config, &nodeAddr, env.state, env.header, tx, &env.header.GasUsed, vmConfig)
		// Bundled tx will be rejected with any receipt.Status other than success.
		// There may be cases where a revert occurs within the EVM, which could result in an attack on a tx sender in an already executed bundle.
		if err != nil || receipt.Status != types.ReceiptStatusSuccessful {
			if err != vm.ErrInsufficientBalance && err != vm.ErrTotalTimeLimitReached {
				markAllTxUnexecutable()
			}
			receiptStatus := ""
			if receipt != nil {
				receiptStatus = strconv.FormatUint(uint64(receipt.Status), 10)
			}
			logger.Warn("ApplyTransaction error, restoring env",
				"blockNum", env.header.Number.String(), "txHash", tx.Hash().String(),
				"error", err, "receiptStatus", receiptStatus,
			)
			restoreEnv()
			if err == nil {
				err = kerrors.ErrRevertedBundleByVmErr
			}
			return err, tx, nil
		}

		env.tcount++
		totalTxSize += uint64(tx.Size())
		txs = append(txs, tx)
		receipts = append(receipts, receipt)
		logs = append(logs, receipt.Logs...)
		if tx.Type() == types.TxTypeEthereumBlob {
			env.blobs += len(tx.BlobHashes())
			*env.header.BlobGasUsed += tx.BlobGas()
		}
	}

	env.size += totalTxSize
	env.txs = append(env.txs, txs...)
	env.receipts = append(env.receipts, receipts...)

	return nil, nil, logs
}
```

**File:** work/worker.go (L956-972)
```go
func (env *Task) shouldDiscardBundle(bundle *builder.Bundle) (bool, error) {
	if !bundle.TargetRequired {
		return false, nil
	}
	if env.tcount == 0 {
		return bundle.TargetTxHash != common.Hash{}, fmt.Errorf("target tx %s does not precede the bundle", bundle.TargetTxHash.Hex())
	} else {
		// if `env.tcount` is not zero, the `bundle.TargetTxHash` must not be empty hash
		if bundle.TargetTxHash != env.txs[env.tcount-1].Hash() {
			return true, fmt.Errorf("target tx %s does not precede the bundle", bundle.TargetTxHash.Hex())
		}
		if env.receipts[env.tcount-1].Status != types.ReceiptStatusSuccessful {
			return true, fmt.Errorf("target tx %s failed with status %d", bundle.TargetTxHash.Hex(), env.receipts[env.tcount-1].Status)
		}
	}
	return false, nil
}
```

**File:** contracts/bindings/auction/Kip249.go (L473-491)
```go
// GetNoncesAndDeposits is a free data retrieval call binding the contract method 0x0339ed37.
//
// Solidity: function getNoncesAndDeposits(address[] searchers) view returns(uint256[] nonces_, uint256[] deposits_)
func (_IAuctionEntryPoint *IAuctionEntryPointSession) GetNoncesAndDeposits(searchers []common.Address) (struct {
	Nonces   []*big.Int
	Deposits []*big.Int
}, error) {
	return _IAuctionEntryPoint.Contract.GetNoncesAndDeposits(&_IAuctionEntryPoint.CallOpts, searchers)
}

// GetNoncesAndDeposits is a free data retrieval call binding the contract method 0x0339ed37.
//
// Solidity: function getNoncesAndDeposits(address[] searchers) view returns(uint256[] nonces_, uint256[] deposits_)
func (_IAuctionEntryPoint *IAuctionEntryPointCallerSession) GetNoncesAndDeposits(searchers []common.Address) (struct {
	Nonces   []*big.Int
	Deposits []*big.Int
}, error) {
	return _IAuctionEntryPoint.Contract.GetNoncesAndDeposits(&_IAuctionEntryPoint.CallOpts, searchers)
}
```
