### Title
Unbounded ECDSA recovery over attacker-controlled signature list before AccountKey type check causes CPU-DoS - ([File: blockchain/types/transaction_signing.go])

### Summary
`Transaction.ValidateSender` (and the tx-pool admission path that calls it) unconditionally recovers a public key for **every** signature present in a Kaia-typed transaction before checking how many signatures the sender's `AccountKey` actually needs. A single crafted transaction can carry many bogus signatures (up to whatever fits under `MaxTxDataSize`), forcing the node to run one `crypto.Ecrecover` per signature during mempool admission — pure CPU cost paid before any gas metering or key-count validation rejects the transaction. This is the same bug class as CVE-2019-19886 (unbounded per-item work triggered by attacker-controlled list length, processed before cheap validity checks, leading to DoS under volume).

### Finding Description
`tx.ValidateSender()` for non-Ethereum-typed transactions calls `SenderPubkey(signer, tx)`, which for `modernSigner`/`EIP155Signer` eventually calls `RecoverTxPubkeys(hash, tx.data.RawSignatureValues(), ...)`: [1](#0-0) 

`RecoverTxPubkeys` just forwards to `TxSignatures.RecoverPubkey`, which iterates over **all** signatures in the transaction and calls `crypto.Ecrecover` (via `s.RecoverPubkey`) for each one, with no bound related to the sender account's actual key configuration: [2](#0-1) [3](#0-2) 

Only *after* all pubkeys are recovered does `ValidateSender` compute `gasKey, err := accKey.SigValidationGas(...)` and call `accountkey.ValidateAccountKey(...)`, i.e. the account-key-aware check (and any gas-based cost accounting) happens after the expensive recovery work is already done: [4](#0-3) 

Crucially, the only gate that limits the number of signatures an attacker can smuggle into a transaction is the general size cap, checked in `TxPool.validateTx` *before* `ValidateSender` is invoked: [5](#0-4) 

There is no separate limit on the number of `TxSignatures` entries tied to `accountkey.MaxNumKeysForMultiSig` (10) at the RLP-decoding or pre-recovery stage — that cap is only enforced when *constructing* signatures locally via `NewTxSignaturesWithValues`: [6](#0-5) 

A transaction built directly (bypassing this helper, e.g. via raw RLP submitted over `eth_sendRawTransaction`) can contain far more than 10 signature tuples — the `TxSignatures` type is just a slice with no explicit upper bound of its own, and `SanityCheckSignatures`/basic decode do not reject oversized lists as long as the whole transaction stays under `MaxTxDataSize`: [7](#0-6) 

Each `(V,R,S)` signature tuple is small in RLP (~70-100 bytes), so a transaction near the `MaxTxDataSize` cap can carry on the order of hundreds of signature tuples, each forcing one costly `Ecrecover` call — for an account that may not even use a multisig key (e.g. a plain `AccountKeyPublic`/`AccountKeyLegacy` account), since the recovery loop runs before the account-key check narrows down how many signatures are actually meaningful.

### Impact Explanation
An unprivileged party (any transaction sender / public-RPC caller) can submit many such oversized-signature-list transactions to a node's tx pool. Each submission forces hundreds of secp256k1 recovery operations during pool admission — work that is not gated by gas and occurs regardless of whether the transaction is ultimately rejected. Sent in volume (as the CVE describes "crafted requests... sent quickly in large volumes"), this degrades tx-pool throughput and can make the node slow or unresponsive to legitimate transaction processing and RPC calls — a Denial of Service analogous to the ModSecurity `Transaction::addRequestHeader` issue, where unbounded per-request work amplified quickly-sent crafted inputs into resource exhaustion.

### Likelihood Explanation
High. The attack requires no privileged access, no valid keys, and no special network position — only the ability to submit RLP-encoded transactions to a public node's tx pool or RPC endpoint (`AddRemote`/`AddRemotes`/`eth_sendRawTransaction`). Signatures do not need to be valid or match any real account; garbage `(V,R,S)` values still force `Ecrecover` to execute (it either returns an error after doing the EC math, or a bogus pubkey) before validation short-circuits. The only constraint is fitting under `MaxTxDataSize`, which still allows hundreds of tuples per transaction.

### Recommendation
- Enforce a hard cap on the number of `TxSignatures` entries immediately after RLP decoding / in `SanityCheckSignatures`, bounded by `accountkey.MaxNumKeysForMultiSig` (or a small constant), rejecting oversized lists before any `Ecrecover` is attempted.
- Reorder `ValidateSender`/`SigValidationGas` logic so the account's expected key-count/type is checked (a cheap state read) before performing per-signature EC recovery, so recovery work scales with the account's actual configuration rather than attacker-supplied list length.
- Consider charging/estimating recovery cost cheaply (e.g., signature count) as part of the tx-pool's very first, pre-state-lookup validation step, alongside the existing `MaxTxDataSize` check.

### Proof of Concept
1. Construct a Kaia-typed transaction (e.g. `TxTypeValueTransfer` or `TxTypeAccountUpdate`) for any `from` address.
2. Instead of signing normally, directly RLP-encode a `TxSignatures` slice with, e.g., 300 arbitrary `(V,R,S)` triples (each a random/garbage value), keeping total encoded transaction size under `MaxTxDataSize` (32 KiB).
3. Submit the resulting raw transaction via `eth_sendRawTransaction` / `TxPool.AddRemote`.
4. Observe that `TxPool.validateTx` passes the size/price checks, then calls `tx.ValidateSender`, which invokes `SenderPubkey` → `RecoverTxPubkeys` → `TxSignatures.RecoverPubkey`, executing `crypto.Ecrecover` ~300 times before the transaction is ultimately rejected (e.g. due to invalid account key match).
5. Repeat with many such transactions from different pool slots/accounts in rapid succession to measure tx-pool admission latency degradation versus a baseline of normally-signed transactions.

### Citations

**File:** blockchain/types/transaction_signing.go (L214-228)
```go
func SenderPubkey(signer Signer, tx *Transaction) ([]*ecdsa.PublicKey, error) {
	if sc := tx.from.Load(); sc != nil {
		sigCache := sc.(sigCachePubkey)
		if signer.IsCompatibleWith(sigCache.signer, tx) {
			return sigCache.pubkey, nil
		}
	}

	pubkey, err := signer.SenderPubkey(tx)
	if err != nil {
		return nil, err
	}
	tx.from.Store(sigCachePubkey{signer: signer, pubkey: pubkey})
	return pubkey, nil
}
```

**File:** blockchain/types/tx_signatures.go (L45-63)
```go
func NewTxSignaturesWithValues(signer Signer, tx *Transaction, txhash common.Hash, prv []*ecdsa.PrivateKey) (TxSignatures, error) {
	if len(prv) == 0 {
		return nil, kerrors.ErrEmptySlice
	}
	if uint64(len(prv)) > accountkey.MaxNumKeysForMultiSig {
		return nil, kerrors.ErrMaxKeysExceed
	}
	txsigs := make(TxSignatures, len(prv))

	for i, p := range prv {
		t, err := NewTxSignatureWithValues(signer, tx, txhash, p)
		if err != nil {
			return nil, err
		}
		txsigs[i] = t
	}

	return txsigs, nil
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

**File:** blockchain/types/tx_signatures.go (L177-197)
```go
// SanityCheckSignatures validates whether the signature values are valid.
// It checks the signatures from the given TxSignatures.
func SanityCheckSignatures(sigs TxSignatures, txType TxType) bool {
	if len(sigs) == 0 {
		return false
	}

	// Legacy and Eth Typed transactions have only one signature.
	sig := sigs[0]

	if txType.IsEthTypedTransaction() {
		v := byte(sig.V.Uint64())
		return crypto.ValidateSignatureValues(v, sig.R, sig.S, false)
	}

	if txType.IsLegacyTransaction() {
		return validateSignature(sig.V, sig.R, sig.S)
	}

	return sigs.ValidateSignature()
}
```

**File:** blockchain/types/tx_signatures.go (L214-218)
```go
// RecoverTxPubkeys returns the public keys derived from txhash and one or more signatures []{v, r, s}.
// Used to recover the sender or fee payer of Kaia typed transactions at tx.ValidateSender() or tx.ValidateFeePayer().
func RecoverTxPubkeys(txhash common.Hash, sigs TxSignatures, homestead bool, vfunc func(*big.Int) *big.Int) ([]*ecdsa.PublicKey, error) {
	return sigs.RecoverPubkey(txhash, homestead, vfunc)
}
```

**File:** blockchain/types/transaction.go (L914-932)
```go
	pubkey, err := SenderPubkey(signer, tx)
	if err != nil {
		return 0, err
	}
	txfrom, ok := tx.data.(TxInternalDataFrom)
	if !ok {
		return 0, errNotTxInternalDataFrom
	}
	from := txfrom.GetFrom()
	accKey := p.GetKey(from)

	gasKey, err := accKey.SigValidationGas(currentBlockNumber, GetRoleTypeForValidation(tx.Type()), len(pubkey))
	if err != nil {
		return 0, err
	}

	if err := accountkey.ValidateAccountKey(currentBlockNumber, from, accKey, pubkey, GetRoleTypeForValidation(tx.Type())); err != nil {
		return 0, ErrInvalidAccountKey
	}
```

**File:** blockchain/tx_pool.go (L884-898)
```go
	// Reject transactions over MaxTxDataSize to prevent DOS attacks
	// Note: Sidecar are not included in the size calculation.
	// Sidecar-specific validation must be done elsewhere.
	if uint64(tx.SizeWithoutBlobTxSidecar()) > MaxTxDataSize {
		return ErrOversizedData
	}

	// Transactions can't be negative. This may never happen using RLP decoded
	// transactions but may occur if you create a transaction using the RPC.
	if tx.Value().Sign() < 0 {
		return ErrNegativeValue
	}

	// Make sure the transaction is signed properly
	gasFrom, err := tx.ValidateSender(pool.signer, pool.currentState, pool.currentBlockNumber)
```
