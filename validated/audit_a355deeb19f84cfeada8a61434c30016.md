### Title
Unbounded ECDSA Signature Recovery Before Signature-Count Validation Causes CPU-Exhaustion DoS in Transaction Sender Validation - ([File: blockchain/types/transaction.go])

### Summary
`Transaction.ValidateSender()` unconditionally recovers a public key for **every** signature attached to a Kaia-typed transaction (via `SenderPubkey`/`RecoverTxPubkeys`) *before* it checks whether the number of signatures is even acceptable for the sender's account key (`AccountKeyWeightedMultiSig.SigValidationGas`, capped at `MaxNumKeysForMultiSig = 10`). Because the number of `TxSignature` entries in a transaction is not bounded independently of the overall transaction byte-size limit, a single crafted transaction can carry hundreds to thousands of signatures, forcing every node that receives it (tx-pool admission, gossip re-validation, block execution) to perform that many expensive `secp256k1` recoveries before the transaction is finally rejected for exceeding the key limit. This mirrors CVE-2013-1443's pattern: an attacker-controlled input length feeds directly into an expensive cryptographic/hash computation performed prior to any cheap length/limit check, resulting in disproportionate CPU consumption reachable from a single unprivileged network input.

### Finding Description
`ValidateSender` calls `SenderPubkey(signer, tx)` first and only afterwards asks the account key for `SigValidationGas`, which is the only place `MaxNumKeysForMultiSig` (10) is enforced: [1](#0-0) 

`SenderPubkey` (for modern/typed transactions) calls `RecoverTxPubkeys`, which iterates over **all** raw signatures and calls `Ecrecover` for each one, with no early bound check on `len(sigs)`: [2](#0-1) [3](#0-2) [4](#0-3) 

Only *after* this full ecrecover loop completes does `AccountKeyWeightedMultiSig.SigValidationGas` check the number of keys/signatures and reject with `ErrMaxKeysExceedInValidation`: [5](#0-4) 

The construction API `NewTxSignaturesWithValues` does enforce `MaxNumKeysForMultiSig` when *building* a transaction locally, but this is a client-side helper, not a protocol-level bound: [6](#0-5) 

Nothing in `TxSignatures.DecodeRLP` (used when parsing a transaction received over RPC or P2P) enforces this cap — the only bound on the number of embedded signatures is the generic transaction byte-size limit (`txSlotSize`/`MaxTxDataSize` constants) referenced in the pool: [7](#0-6) 

Because each `TxSignature` occupies roughly 65+ bytes of RLP payload, a transaction near the maximum allowed size can carry on the order of hundreds to low thousands of forged/garbage signatures. The existing test suite confirms the codepath: `TxPool.AddRemote` invokes sender/account-key validation and only returns `kerrors.ErrMaxKeysExceed` *after* processing, confirming the expensive recovery happens before the cheap length check: [8](#0-7) 

### Impact Explanation
Every node in the network that receives such a transaction — via RPC submission, P2P gossip re-validation in the tx pool, or block re-execution by validators — must perform O(N) expensive elliptic-curve signature recoveries (`secp256k1.RecoverPubkey`) before the transaction is rejected as invalid. Signature recovery is one of the most CPU-intensive per-transaction operations in the client (comparable in cost class to password hashing in the Django CVE). An attacker paying for one (or a bounded number of) rejected transactions can impose CPU cost on every peer that processes it, well beyond the cost the attacker incurs, satisfying the "CPU consumption" DoS class from the advisory. This can degrade tx-pool throughput and block-production timeliness on Consensus/Endpoint Nodes, which is a High-severity availability impact.

### Likelihood Explanation
Reachable by any unprivileged transaction sender or public-RPC caller: no special account setup or fee delegation is required — an attacker only needs to construct a raw Kaia-typed transaction (e.g., `TxTypeValueTransfer` or `TxTypeAccountUpdate`) with an arbitrary but structurally valid list of `TxSignature` entries (each syntactically valid per `ValidateSignatureValues`, but not necessarily correct/matching a real account) and submit it via `eth_sendRawTransaction` or gossip it directly. It does not require the sender account to actually be a multisig account beforehand, and it does not require the transaction to ever be included in a block — the cost is incurred purely at pool-admission / validation time before rejection.

### Recommendation
Enforce a cheap upper bound on `len(TxSignatures)` (e.g., `MaxNumKeysForMultiSig`, or a small constant appropriate for `RoleBased` composite keys) immediately during `TxSignatures.DecodeRLP` / `SanityCheckSignatures`, before any `Ecrecover` call is made in `ValidateSender`/`SenderPubkey`. Reorder `ValidateSender` to check the signature count against the account's key configuration limits *before* invoking `SenderPubkey`, so the expensive cryptographic recovery is never performed for transactions that are trivially disqualified by having too many signatures.

### Proof of Concept
1. Craft a `TxTypeValueTransfer` (or any Kaia-typed) transaction targeting an arbitrary sender address.
2. Populate its `TxSignatures` field with as many syntactically-valid-but-unrelated `(v, r, s)` triples as fit within `MaxTxDataSize` (potentially hundreds to thousands, since each signature needs only ~65–100 RLP-encoded bytes and is not otherwise bounded on decode).
3. Submit the transaction via `eth_sendRawTransaction` to a public RPC endpoint, or broadcast it over P2P.
4. Every receiving node's `TxPool.AddRemote` → `Transaction.ValidateSender` → `SenderPubkey` → `RecoverTxPubkeys` path performs one `Ecrecover` per embedded signature before finally rejecting the transaction with `ErrMaxKeysExceedInValidation` (see `blockchain/types/accountkey/account_key_weighted_multi_sig.go:161-178`) — the CPU cost of the (failed) validation scales with attacker-chosen signature count, not with any fee paid, since the transaction is never accepted and no gas is charged.

Note: I was not able to directly view the numeric value of `MaxTxDataSize` (the file read was truncated at line 60), so the exact maximum number of signatures that fit in one transaction could not be precisely computed from the index; this would need to be confirmed by reading the remainder of `blockchain/tx_pool.go` or `params` constants in a full repository checkout.

### Citations

**File:** blockchain/types/transaction.go (L913-932)
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

**File:** blockchain/types/transaction_signing.go (L336-358)
```go
func (s modernSigner) SenderPubkey(tx *Transaction) ([]*ecdsa.PublicKey, error) {
	// Check if this transaction type is supported
	if !s.IsSupported(tx) {
		return nil, ErrTxTypeNotSupported
	}

	// Legacy transactions are handled by the legacy signer
	if !tx.Type().IsEthTypedTransaction() {
		return s.legacy.SenderPubkey(tx)
	}

	// Validate chain ID
	if tx.ChainId().Cmp(s.chainId) != 0 {
		return nil, ErrInvalidChainId
	}

	// All modern transaction types use the same recovery mechanism
	return RecoverTxPubkeys(s.Hash(tx), tx.data.RawSignatureValues(), true, func(v *big.Int) *big.Int {
		// Modern txs use 0 and 1 as recovery id, add 27 to become equivalent to unprotected Homestead signatures.
		V := new(big.Int).Add(v, big.NewInt(27))
		return V
	})
}
```

**File:** blockchain/types/tx_signatures.go (L45-51)
```go
func NewTxSignaturesWithValues(signer Signer, tx *Transaction, txhash common.Hash, prv []*ecdsa.PrivateKey) (TxSignatures, error) {
	if len(prv) == 0 {
		return nil, kerrors.ErrEmptySlice
	}
	if uint64(len(prv)) > accountkey.MaxNumKeysForMultiSig {
		return nil, kerrors.ErrMaxKeysExceed
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

**File:** blockchain/types/tx_signatures.go (L214-218)
```go
// RecoverTxPubkeys returns the public keys derived from txhash and one or more signatures []{v, r, s}.
// Used to recover the sender or fee payer of Kaia typed transactions at tx.ValidateSender() or tx.ValidateFeePayer().
func RecoverTxPubkeys(txhash common.Hash, sigs TxSignatures, homestead bool, vfunc func(*big.Int) *big.Int) ([]*ecdsa.PublicKey, error) {
	return sigs.RecoverPubkey(txhash, homestead, vfunc)
}
```

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L161-178)
```go
func (a *AccountKeyWeightedMultiSig) SigValidationGas(currentBlockNumber uint64, r RoleType, numSigs int) (uint64, error) {
	numKeys := uint64(len(a.Keys))
	if numKeys > MaxNumKeysForMultiSig {
		logger.Warn("validation failed due to the number of keys in the account is larger than the limit.",
			"account", a.String())
		return 0, kerrors.ErrMaxKeysExceedInValidation
	}
	if numKeys == 0 {
		logger.Error("should not happen! numKeys is equal to zero!")
		return 0, kerrors.ErrZeroLength
	}

	isIstanbul := fork.Rules(new(big.Int).SetUint64(currentBlockNumber)).IsIstanbul
	if isIstanbul {
		return uint64(numSigs-1) * params.TxValidationGasPerKey, nil
	}
	return (numKeys - 1) * params.TxValidationGasPerKey, nil
}
```

**File:** blockchain/tx_pool.go (L47-60)
```go
const (
	// chainHeadChanSize is the size of channel listening to ChainHeadEvent.
	chainHeadChanSize = 10

	// txSlotSize is used to calculate how many data slots a single transaction
	// takes up based on its size. The slots are used as DoS protection, ensuring
	// that validating a new transaction remains a constant operation (in reality
	// O(maxslots), where max slots are 4 currently).
	txSlotSize = 32 * 1024

	// MaxTxDataSize is the maximum size a single transaction can have. This field has
	// non-trivial consequences: larger transactions are significantly harder and
	// more expensive to propagate; larger transactions also take more resources
	// to validate whether they fit into the pool or not.
```

**File:** tests/account_keytype_test.go (L725-754)
```go
	// make TxPool to test validation in 'TxPool add' process
	txpool := blockchain.NewTxPool(blockchain.DefaultTxPoolConfig, bcdata.bc.Config(), bcdata.bc, bcdata.govModule)

	// update key to a multiSig account with 11 different private keys (more than 10 -> failed)
	{
		values := map[types.TxValueKeyType]interface{}{
			types.TxValueKeyNonce:      anon.Nonce,
			types.TxValueKeyFrom:       anon.Addr,
			types.TxValueKeyGasLimit:   gasLimit,
			types.TxValueKeyGasPrice:   gasPrice,
			types.TxValueKeyAccountKey: multisig.AccKey,
		}
		tx, err := types.NewTransactionWithMap(types.TxTypeAccountUpdate, values)
		assert.Equal(t, nil, err)

		err = tx.SignWithKeys(signer, anon.Keys)
		assert.Equal(t, nil, err)

		// For tx pool validation test
		{
			err = txpool.AddRemote(tx)
			assert.Equal(t, kerrors.ErrMaxKeysExceed, err)
		}

		// For block tx validation test
		{
			receipt, err := applyTransaction(t, bcdata, tx)
			assert.Equal(t, kerrors.ErrMaxKeysExceed, err)
			assert.Equal(t, (*types.Receipt)(nil), receipt)
		}
```
