Based on my investigation, I found a concrete analog in this codebase.

### Title
Unbounded `TxSignatures` array in RLP-decoded transactions causes CPU/allocation amplification before any gas or fee is charged, in `TxPool.validateTx` - (File: blockchain/tx_pool.go, blockchain/types/tx_signatures.go)

### Summary
The advisory's bug class is: a caller-controlled repeated/multi-valued container is parsed and cryptographically processed once per element with no aggregate size cap, letting an unauthenticated caller multiply CPU/allocation cost far beyond the "cost" it pays. In Kaia, `TxPool.AddRemote`/`validateTx` performs exactly this pattern on `TxSignatures` before any gas fee is actually collected from the sender.

### Finding Description
`Transaction.ValidateSender` (called from `TxPool.validateTx`, [1](#0-0) ) calls `SenderPubkey`, which for Kaia typed transactions resolves to `TxSignatures.RecoverPubkey`, an unbounded loop that runs one ECDSA `Ecrecover` per element of `TxSignatures`: [2](#0-1) 

`TxSignatures` is decoded directly from attacker-supplied RLP with no length cap at decode time — `TxInternalData.DecodeRLP` only checks `SanityCheckSignatures`, which validates the *first* signature's `V/R/S` shape and, for non-legacy/non-eth-typed types, calls `sigs.ValidateSignature()` (an O(n) loop, not a length bound): [3](#0-2) [4](#0-3) [5](#0-4) 

The only place a maximum signature/key count (`accountkey.MaxNumKeysForMultiSig`, `= 10`) is enforced is in the *signing* helper `NewTxSignaturesWithValues` used by honest wallets when creating a transaction: [6](#0-5) 
That check does not run on the receive/decode path. Nothing in `DecodeRLP`, `SanityCheckSignatures`, or `TxPool.validateTx` rejects a `TxSignatures` slice with (e.g.) thousands of entries before `ValidateSender` recovers a public key for every single one via `Ecrecover`.

Only after all pubkeys are recovered does `AccountKeyRoleBased.SigValidationGas`/`AccountKeyWeightedMultiSig.Validate` check `len(recoveredKeys) > len(a.Keys)` and reject the tx — i.e., the expensive cryptographic work happens *before* the account-key-length check and before the transaction is confirmed to pay for the corresponding gas (`SigValidationGas` is only used to size the *cost* charged to a valid tx; a bloated, ultimately-rejected tx pays nothing to the pool, since `AddRemote` returns an error and the tx is dropped, but the CPU is already spent).

This mirrors the external report's root cause precisely: an aggregation loop over a caller-supplied multi-element field performs full per-element expensive work (parsing/crypto recovery) with no global item-count budget enforced ahead of time, so cost is not bounded by what the sender actually commits to pay.

### Impact Explanation
A single unauthenticated RPC caller can submit `eth_sendRawTransaction`/`kaia_sendRawTransaction` transactions of type `TxTypeValueTransfer`/`TxTypeAccountUpdate`/etc. whose RLP-encoded `TxSignatures` list contains a very large number of (even syntactically well-formed but invalid) `(V,R,S)` triples, up to `MaxTxDataSize` bytes allowed by pool admission (checked later in `validateTx` at line 887, i.e. *after* `SanityCheckSignatures`/before `ValidateSender`—so size checks and expensive recovery both happen on the same request path, and the size cap does not zero out the disproportionate cost since each signature triple is small (~96 bytes) letting thousands fit in an oversized-but-permitted payload). Each submission forces the node to run one `secp256k1.Ecrecover` (a relatively expensive operation, ~100k+ computation cost units per the `EcrecoverComputationCost`/`ValidateSenderPerSigComputationCost` constants used elsewhere in the codebase, cf. [7](#0-6) ) per signature, all for a transaction that will ultimately be rejected with no fee paid. Repeated submissions from many senders/many such transactions amplify CPU load on public RPC / mempool-serving Endpoint Nodes, degrading transaction-pool processing and potentially node responsiveness — a remote DoS amplification consistent with CWE-400/CWE-770, reachable purely via a submitted transaction from any public-RPC caller.

### Likelihood Explanation
High likelihood of reachability: any external actor can call the public `sendRawTransaction`-style RPC with an arbitrary RLP payload; no authentication, staking, or prior on-chain state is required. The vulnerable code path (`TxPool.validateTx` → `ValidateSender` → `SenderPubkey` → `TxSignatures.RecoverPubkey`) is on the hot path for every submitted Kaia-typed transaction, and the missing bound is a straightforward gap (bound exists only in the wallet-side constructor, not the network-facing decode/validate path).

### Recommendation
Enforce `len(TxSignatures) <= accountkey.MaxNumKeysForMultiSig` (and equivalently for `FeePayerSignatures`) as an early, cheap structural check in `SanityCheckSignatures` / `Transaction.DecodeRLP`, before any `Ecrecover` calls are performed, so that oversized signature lists are rejected at O(1) instead of O(n) expensive-crypto cost. Additionally, consider rejecting such transactions in `TxPool.validateTx` prior to invoking `ValidateSender` if `len(tx.RawSignatureValues()) > MaxNumKeysForMultiSig`.

### Proof of Concept
Not independently executed (analysis based on static code review only, per the ask-only nature of this task). Conceptual PoC: craft an RLP-encoded `TxTypeAccountUpdate` (or any Kaia typed tx) whose `TxSignatures` field contains e.g. 500–1000 syntactically valid-shaped but cryptographically bogus `(V,R,S)` triples (keeping total tx size under `MaxTxDataSize`), and submit repeatedly via `sendRawTransaction`; measure CPU time spent in `TxPool.AddRemote`/`ValidateSender` versus a normal single-signature transaction of similar byte size. I was not able to execute this against a live node; this should be validated by running the target repository's test/benchmark harness (e.g., extending `tests/evm_op_benchmark_test.go`'s existing `validateSenderMultisig*` benchmarks, which already demonstrate per-signature cost scaling, to the tx-pool admission path instead of the precompile).

### Citations

**File:** blockchain/tx_pool.go (L897-901)
```go
	// Make sure the transaction is signed properly
	gasFrom, err := tx.ValidateSender(pool.signer, pool.currentState, pool.currentBlockNumber)
	if err != nil {
		return types.ErrSender(err)
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

**File:** blockchain/types/tx_signatures.go (L91-108)
```go
func (t TxSignatures) ValidateSignature() bool {
	txSig, err := t.getDefaultSig()
	if err != nil {
		return false
	}

	cid := txSig.ChainId()
	for _, s := range t {
		if s.ValidateSignature() == false {
			return false
		}
		if cid.Cmp(s.ChainId()) != 0 {
			return false
		}
	}

	return true
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

**File:** blockchain/types/transaction.go (L240-254)
```go
func (tx *Transaction) DecodeRLP(s *rlp.Stream) error {
	serializer := newTxInternalDataSerializer()
	if err := s.Decode(serializer); err != nil {
		return err
	}

	if !SanityCheckSignatures(serializer.tx.RawSignatureValues(), serializer.tx.Type()) {
		return ErrInvalidSig
	}

	size := calculateTxSize(serializer.tx)
	tx.setDecoded(serializer.tx, int(size))

	return nil
}
```

**File:** params/computation_cost_params.go (L139-157)
```go
	EcrecoverComputationCost                       = 113150
	Sha256PerWordComputationCost                   = 100
	Sha256BaseComputationCost                      = 1000
	Ripemd160PerWordComputationCost                = 10
	Ripemd160BaseComputationCost                   = 100
	IdentityPerWordComputationCost                 = 0
	IdentityBaseComputationCost                    = 0
	BigModExpPerGasComputationCost                 = 10
	BigModExpBaseComputationCost                   = 100
	Bn256AddComputationCost                        = 8000
	Bn256ScalarMulComputationCost                  = 100000
	Bn256ParingBaseComputationCost                 = 2000000
	Bn256ParingPerPointComputationCost             = 1000000
	BlobTxPointEvaluationPrecompileComputationCost = 2200000
	VMLogPerByteComputationCost                    = 0
	VMLogBaseComputationCost                       = 10
	FeePayerComputationCost                        = 10
	ValidateSenderPerSigComputationCost            = 180000
	ValidateSenderBaseComputationCost              = 10000
```
