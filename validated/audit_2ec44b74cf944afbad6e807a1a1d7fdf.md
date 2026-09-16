### Title
Unbounded Multi-Signature `ecrecover` Loop in Kaia Transaction Sender/Fee-Payer Recovery Allows CPU-Exhaustion DoS on TxPool Workers - ([File: blockchain/types/tx_signatures.go])

### Summary
A single unprivileged transaction submitted via public RPC or p2p can carry an attacker-controlled number of `TxSignature` entries (up to the ~128KB `MaxTxDataSize` limit, i.e. potentially hundreds to ~1800 signatures). `TxSignatures.RecoverPubkey` unconditionally performs one expensive secp256k1 `ecrecover` per signature entry, *before* any check that the signature count is sane for the account's `AccountKey` (the count-vs-key-count check lives inside `AccountKeyWeightedMultiSig.Validate`/`AccountKeyRoleBased`, which runs only after all recoveries complete). This mirrors the Elasticsearch analog: a small, cheaply-submitted "document" (transaction) causes a bounded worker thread (the `senderCacher` pool / txpool validation goroutine) to spend disproportionate CPU before the request is ultimately rejected.

### Finding Description
`TxSignatures.RecoverPubkey` loops over every signature in the tx and calls `ecrecover` for each, with no upper bound check performed first: [1](#0-0) 

This is reached from `SenderPubkey`/`SenderFeePayer` for any Kaia-typed transaction (`modernSigner.SenderPubkey`, `EIP155Signer.SenderFeePayer`), which are invoked both by the background `senderCacher` pool used to pre-warm sender caches for incoming batches, and directly inside `tx.ValidateSender`/`tx.ValidateFeePayer` during `TxPool.validateTx`: [2](#0-1) [3](#0-2) 

Only *after* all pubkeys have been recovered does the code check whether the number of recovered signatures is compatible with the account's registered key count: [4](#0-3) 

The number of `TxSignature` entries in a transaction is bounded only by the generic `MaxTxDataSize` (128KB) DoS guard on total encoded tx size, not by any signature-count-specific limit: [5](#0-4) [6](#0-5) 

Because a `TxSignature` (V,R,S) RLP-encodes to roughly 60-70 bytes, a single 128KB transaction can carry on the order of 1,500-1,800 signatures, all of which get `ecrecover`'d (a computationally expensive elliptic-curve operation, on the order of tens of microseconds each) before the mismatch against the target account's key count (capped at `MaxNumKeysForMultiSig = 10`) is detected and the transaction rejected: [7](#0-6) 

The gas-based cost accounting for signature validation (`SigValidationGas`) is only computed and checked *after* `ValidateSender`/`ValidateFeePayer` have already performed all the recoveries: [8](#0-7) 

so there is no economic disincentive paid up front for the CPU actually consumed — the attacker can force the work to happen for free before the node discovers the tx is invalid and discards it (no gas is charged for invalid/rejected transactions).

### Impact Explanation
Any transaction pool worker (the `senderCacher` background goroutines, sized at `~2/3 * NumCPU`) or the txpool's synchronous `add`/`validateTx` path can be made to spend a disproportionate amount of CPU time processing a single, cheap, low-gas transaction, because the number of costly `ecrecover` calls is attacker-controlled and unbounded (up to ~1,800) regardless of the target account's real key count (max 10). Submitting a modest stream of such maximally-padded transactions can saturate the fixed-size sender-cacher worker pool and/or the txpool's `addTxsLocked`/`validateTx` critical path, degrading transaction admission/propagation throughput for the whole node — a direct analog of the reported Elasticsearch "one small malicious document occupies a bounded worker thread pool disproportionately" bug class. This does not require any special privilege beyond the ability to submit an arbitrary transaction over RPC/p2p.

### Likelihood Explanation
High likelihood of reachability: any public-RPC caller or p2p peer can submit an arbitrary Kaia-typed transaction (e.g., `TxTypeValueTransfer`, `TxTypeAccountUpdate`, fee-delegated variants) with a crafted, oversized `TxSignatures` list up to the 128KB size cap, targeting any account (the target account's real key type does not need to match — the recovery loop runs before the account-key compatibility check). No special account setup, high gas price, or privileged access is required; only ordinary "submit a transaction" capability.

### Recommendation
Enforce a maximum number of signatures per transaction (e.g., bounded by `accountkey.MaxNumKeysForMultiSig` plus a small margin, or a fixed protocol constant) as an early, cheap length check in `TxSignatures.RecoverPubkey` / `RecoverTxPubkeys`, and in `SanityCheckSignatures`, before any `ecrecover` calls are performed. This check should occur prior to invoking `signer.SenderPubkey`/`SenderFeePayer` in both `tx_cacher.go`'s `cacheSender` and `TxPool.validateTx`, so that a transaction with an implausible number of signatures is rejected in O(1) rather than performing O(numSigs) expensive cryptographic operations first.

### Proof of Concept
1. Construct any Kaia-typed transaction (e.g. `TxTypeValueTransfer`) targeting an arbitrary existing account.
2. Populate its `TxSignatures` field with as many `(V,R,S)` triples as fit under `MaxTxDataSize` (~128KB), each triple can contain arbitrary/garbage or replayed valid-format values (they need not verify correctly, since `ecrecover` will simply be attempted for each) — this yields roughly 1,500+ signature entries.
3. Submit the transaction via `eth_sendRawTransaction` (or p2p `TxMsg`).
4. Observe that `TxPool.add` → `validateTx` → `tx.ValidateSender` → `SenderPubkey` → `RecoverTxPubkeys` → `TxSignatures.RecoverPubkey` performs one `ecrecover` per signature entry (≈1,500 expensive EC operations) before the eventual `ErrInvalidAccountKey`/signature-count mismatch is returned, consuming disproportionate CPU on the validating goroutine/senderCacher worker for a single cheap, ultimately-rejected transaction — repeatable at negligible cost to the attacker to degrade node tx-processing throughput.

### Citations

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

**File:** blockchain/tx_cacher.go (L72-97)
```go
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

**File:** blockchain/tx_pool.go (L57-62)
```go
	// MaxTxDataSize is the maximum size a single transaction can have. This field has
	// non-trivial consequences: larger transactions are significantly harder and
	// more expensive to propagate; larger transactions also take more resources
	// to validate whether they fit into the pool or not.
	// TODO-Kaia: Change the name to clarify what it means. It means the max length of the transaction.
	MaxTxDataSize = 4 * txSlotSize // 128KB
```

**File:** blockchain/tx_pool.go (L884-889)
```go
	// Reject transactions over MaxTxDataSize to prevent DOS attacks
	// Note: Sidecar are not included in the size calculation.
	// Sidecar-specific validation must be done elsewhere.
	if uint64(tx.SizeWithoutBlobTxSidecar()) > MaxTxDataSize {
		return ErrOversizedData
	}
```

**File:** blockchain/tx_pool.go (L897-901)
```go
	// Make sure the transaction is signed properly
	gasFrom, err := tx.ValidateSender(pool.signer, pool.currentState, pool.currentBlockNumber)
	if err != nil {
		return types.ErrSender(err)
	}
```

**File:** blockchain/tx_pool.go (L991-998)
```go
	intrGas, err := tx.IntrinsicGas(pool.currentBlockNumber)
	sigValGas := gasFrom + gasFeePayer
	if err != nil {
		return err
	}
	if tx.Gas() < intrGas+sigValGas {
		return ErrIntrinsicGas
	}
```

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L33-37)
```go
const (
	// TODO-Kaia-MultiSig: Need to fix the maximum number of keys allowed for an account.
	// NOTE-Kaia-MultiSig: This value should not be reduced. If it is reduced, there is a case:
	// - the tx validation will be failed if the sender has larger keys.
	MaxNumKeysForMultiSig = uint64(10)
```

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L86-97)
```go
func (a *AccountKeyWeightedMultiSig) Validate(currentBlockNumber uint64, r RoleType, recoveredKeys []*ecdsa.PublicKey, from common.Address) bool {
	if a.Threshold == 0 {
		return false
	}
	isIstanbul := fork.Rules(new(big.Int).SetUint64(currentBlockNumber)).IsIstanbul

	// Validation 1. if isIstanbul is true, check whether the signature number exceeds key number
	if isIstanbul && len(recoveredKeys) > len(a.Keys) {
		logger.Debug("AccountKeyWeightedMultiSig validation failed and number of signatures exceeds key number",
			"numSigs", len(recoveredKeys), "numKeys", len(a.Keys))
		return false
	}
```
