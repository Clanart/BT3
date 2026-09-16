### Title
Missing panic handler on transaction sender-recovery worker pool crashes the entire node on malicious transaction submission - (File: blockchain/tx_cacher.go)

### Summary
The `txSenderCacher` background worker pool used to concurrently recover transaction senders/fee-payers is missing a `recover()` guard around its per-goroutine work loop, unlike other worker pools in the same codebase (e.g. `node/cn/handler.go`'s `processMsg`) that explicitly install panic recovery. A panic triggered while processing an attacker-supplied transaction inside one of these fixed worker goroutines is unrecoverable and crashes the entire Kaia node process, since an unhandled panic in any goroutine terminates the whole Go process.

### Finding Description
`newTxSenderCacher` starts a fixed number of long-lived goroutines running `cache()`, which pulls signature-recovery tasks from a shared channel and calls `cacheSender` for each transaction: [1](#0-0) 

`cacheSender` invokes `types.SenderFrom`, `types.SenderPubkey`, and, for fee-delegated transactions, `types.SenderFeePayerPubkey` directly on attacker-controlled transaction fields, with no `recover()` wrapper anywhere in the call chain: [2](#0-1) 

This differs from the pattern used elsewhere in Kaia for worker goroutines that process untrusted network input. For example, `ProtocolManager.processMsg`, which also runs as a persistent pool of goroutines processing peer-supplied messages, explicitly wraps its loop in a `defer/recover`: [3](#0-2) 

Similarly, `datasync/dbsyncer/query_engine.go`'s worker functions install `recover()` around per-task processing: [4](#0-3) 

The `txSenderCacher.recover` and `recoverFromBlocks` methods, which feed the vulnerable worker pool, are called directly from the transaction pool when new transactions are submitted (i.e., reachable from any unprivileged transaction sender via `eth_sendRawTransaction`/public RPC), and from block insertion: [5](#0-4) 

The reachable functions perform elliptic-curve signature recovery and byte manipulation on RLP-decoded, attacker-supplied `V/R/S` fields (`recoverPlainCommon`, `Ecrecover`, `TxSignatures.RecoverPubkey`, etc.): [6](#0-5) [7](#0-6) 

I was unable to fully verify a concrete panic-triggering input within the time available (e.g., a nil-`big.Int` or index-out-of-range condition reachable through a crafted RLP transaction that bypasses upstream decoding checks). The architectural weakness — lack of panic isolation for this worker pool despite being fed with fully attacker-controlled data — is confirmed, but the specific panic trigger inside `crypto.Ecrecover`/`recoverPlainCommon`/`TxSignature` decode paths was not conclusively identified.

### Impact Explanation
If any panic occurs inside `cacheSender` (via a future bug, an edge case in signature/account-key decoding, or a currently-unknown malformed transaction encoding that passes RLP decoding but violates an implicit invariant assumed by the EC-recovery code), the goroutine crashes and, per Go's runtime semantics, brings down the entire node process — including consensus and RPC serving. Because `senderCacher.recover` is invoked on the hot path for every transaction submitted to the pool by any public, unprivileged sender, this constitutes a remotely triggerable denial-of-service against any node (including validators/CNs) that admits the transaction into its pool.

### Likelihood Explanation
Likelihood depends on the existence of an actual panic-inducing malformed transaction, which was not conclusively found in the time available; this is a structural resilience gap (no panic isolation on a worker pool processing 100% attacker-controlled input), not a demonstrated exploit. Given the strict validation rules for this analog exercise (must prove root cause with concrete unauthorized-value/DoS impact), this should be treated as a **hardening gap** rather than a confirmed exploitable bug absent a proven panic trigger.

### Recommendation
Add a `defer/recover` guard inside `txSenderCacher.cache()` (and any other worker-pool functions lacking one, following the reth fix's pattern of applying it uniformly to *all* worker pools) so that a panic in `cacheSender` logs the error and continues processing rather than crashing the node process:
```go
func (cacher *txSenderCacher) cache() {
    for task := range cacher.tasks {
        func() {
            defer func() {
                if r := recover(); r != nil {
                    logger.Error("panic recovered in senderCacher", "err", r)
                }
            }()
            for i := 0; i < len(task.txs); i += task.inc {
                cacheSender(task.signer, task.txs[i])
            }
        }()
    }
}
```
Additionally, audit other background worker pools in the codebase (e.g. `storage/database/dynamodb.go`'s `createBatchWriteWorker`, `datasync/downloader`, `datasync/fetcher.insertWorker`) for the same missing-panic-isolation pattern.

### Proof of Concept
Not established — no concrete malformed-transaction input was verified within the available investigation time to actually trigger a panic inside `cacheSender`'s call chain (`types.SenderFrom`/`SenderPubkey`/`SenderFeePayerPubkey` → `RecoverTxSender`/`RecoverTxPubkeys` → `recoverPlainCommon`/`crypto.Ecrecover`). A concrete PoC would require identifying a transaction encoding that passes RLP/basic decoding but causes a nil-pointer dereference, index-out-of-range, or big.Int operation panic inside this call chain — this was not confirmed and should be validated via fuzzing/unit testing before treating this as more than a structural resilience gap.

### Citations

**File:** blockchain/tx_cacher.go (L61-97)
```go
func newTxSenderCacher(threads int) *txSenderCacher {
	cacher := &txSenderCacher{
		tasks:   make(chan *txSenderCacherRequest, threads),
		threads: threads,
	}
	for range threads {
		go cacher.cache()
	}
	return cacher
}

// cacheSender calls SenderXXX functions based on the tx types.
// If a legacy transaction, it calls SenderFrom() to cache an address into `Transaction.from`.
// Otherwise, it calls SenderPubkey() to cache a pubkey into `Transaction.from`.
// In addition, if a transaction is a fee-delegated transaction, it also caches a pubkey into `Transaction.feePayer`.
func cacheSender(signer types.Signer, tx *types.Transaction) {
	if tx.IsEthereumTransaction() {
		types.SenderFrom(signer, tx)
		return
	}

	types.SenderPubkey(signer, tx)

	if tx.IsFeeDelegatedTransaction() {
		types.SenderFeePayerPubkey(signer, tx)
	}
}

// cache is an infinite loop, caching transaction senders from various forms of
// data structures.
func (cacher *txSenderCacher) cache() {
	for task := range cacher.tasks {
		for i := 0; i < len(task.txs); i += task.inc {
			cacheSender(task.signer, task.txs[i])
		}
	}
}
```

**File:** blockchain/tx_cacher.go (L99-134)
```go
// recover recovers the senders from a batch of transactions and caches them
// back into the same data structures. There is no validation being done, nor
// any reaction to invalid signatures. That is up to calling code later.
func (cacher *txSenderCacher) recover(signer types.Signer, txs []*types.Transaction) {
	// If there's nothing to recover, abort
	if len(txs) == 0 {
		return
	}
	// Ensure we have meaningful task sizes and schedule the recoveries
	tasks := cacher.threads
	if len(txs) < tasks*4 {
		tasks = (len(txs) + 3) / 4
	}
	for i := 0; i < tasks; i++ {
		cacher.tasks <- &txSenderCacherRequest{
			signer: signer,
			txs:    txs[i:],
			inc:    tasks,
		}
	}
}

// recoverFromBlocks recovers the senders from a batch of blocks and caches them
// back into the same data structures. There is no validation being done, nor
// any reaction to invalid signatures. That is up to calling code later.
func (cacher *txSenderCacher) recoverFromBlocks(signer types.Signer, blocks []*types.Block) {
	count := 0
	for _, block := range blocks {
		count += len(block.Transactions())
	}
	txs := make([]*types.Transaction, 0, count)
	for _, block := range blocks {
		txs = append(txs, block.Transactions()...)
	}
	cacher.recover(signer, txs)
}
```

**File:** node/cn/handler.go (L697-704)
```go
func (pm *ProtocolManager) processMsg(msgCh <-chan p2p.Msg, p Peer, addr common.Address, errCh chan<- error) {
	defer func() {
		if err := recover(); err != nil {
			logger.Error("stacktrace from panic: \n" + string(debug.Stack()))
			logger.Warn("the panic is recovered", "panicErr", err)
			errCh <- errUnknownProcessingError
		}
	}()
```

**File:** datasync/dbsyncer/query_engine.go (L96-102)
```go
	defer func() {
		// recover from panic caused by writing to a closed channel
		if r := recover(); r != nil {
			logger.Error("channel closed", "err", r)
			return
		}
	}()
```

**File:** blockchain/types/transaction_signing.go (L699-722)
```go
func recoverPlainCommon(sighash common.Hash, R, S, Vb *big.Int, homestead bool) ([]byte, error) {
	if Vb.BitLen() > 8 {
		return []byte{}, ErrInvalidSig
	}
	V := byte(Vb.Uint64() - 27)
	if !crypto.ValidateSignatureValues(V, R, S, homestead) {
		return []byte{}, ErrInvalidSig
	}
	// encode the snature in uncompressed format
	r, s := R.Bytes(), S.Bytes()
	sig := make([]byte, crypto.SignatureLength)
	copy(sig[32-len(r):32], r)
	copy(sig[64-len(s):64], s)
	sig[crypto.RecoveryIDOffset] = V
	// recover the public key from the snature
	pub, err := crypto.Ecrecover(sighash[:], sig)
	if err != nil {
		return []byte{}, err
	}
	if len(pub) == 0 || pub[0] != 4 {
		return []byte{}, errors.New("invalid public key")
	}
	return pub, nil
}
```

**File:** blockchain/types/tx_signatures.go (L134-146)
```go
func (t TxSignatures) RecoverPubkey(txhash common.Hash, homestead bool, vfunc func(*big.Int) *big.Int) ([]*ecdsa.PublicKey, error) {
	var err error

	pubkeys := make([]*ecdsa.PublicKey, len(t))
	for i, s := range t {
		pubkeys[i], err = s.RecoverPubkey(txhash, homestead, vfunc)
		if err != nil {
			return nil, err
		}
	}

	return pubkeys, nil
}
```
