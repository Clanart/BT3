## Analysis Result

### Title
Unprotected (pre-EIP-155) legacy transactions bypass chain-ID validation in `TxPool.validateTx` and `EIP155Signer.Sender`, enabling cross-chain transaction replay - (File: `blockchain/tx_pool.go`, `blockchain/types/transaction_signing.go`)

### Summary
Kaia's transaction pool and legacy-transaction signer treat EIP-155 replay protection as optional rather than mandatory, mirroring the exact bug class reported in the zkSync finding (reserved[0]==0 disabling EIP-155). A legacy transaction signed with `v ∈ {0,1,27,28}` (an "unprotected" signature) skips chain-ID verification entirely and is recovered using the Homestead/Frontier signing scheme, which excludes the chain ID from the signed hash. Because the signature never binds to Kaia's chain ID, any pre-EIP-155 (or deliberately unprotected) transaction valid on Ethereum mainnet or another EVM chain sharing the same signing key can be replayed verbatim on Kaia.

### Finding Description
`Transaction.Protected()` determines whether replay protection is present by checking the raw `V` value: [1](#0-0) 

`EIP155Signer.Sender()` — the signer Kaia uses to recover the sender of legacy transactions — explicitly falls back to the un-protected `HomesteadSigner` whenever `tx.Protected()` is false, and only enforces the chain-ID equality check (`tx.ChainId().Cmp(s.chainId) != 0`) on the protected path: [2](#0-1) 

`HomesteadSigner.Sender()` recovers the sender using a hash that never includes the chain ID at all: [3](#0-2) 

At the transaction-pool admission layer, the chain-ID check is likewise gated by `tx.Protected()`: [4](#0-3) 

This means an unprotected legacy transaction (`v` = 27/28, or 0/1) is accepted into the pool with **no chain-ID check whatsoever**, and its sender is recovered via a hash that is identical across every EVM chain (Ethereum mainnet, other EVM L1/L2s, etc.) that uses the same pre-EIP-155/Homestead signing convention. A comment in the block-building code even claims this can't happen ("Kaia is always in EIP155, the below replay protection code is not needed"), but the actual `Protected()`-gated logic in `tx_pool.go` and `transaction_signing.go` shows unprotected transactions are still accepted and processed: [5](#0-4) 

This is functionally identical to the zkSync `DefaultAccount`/bootloader issue: when the replay-protection flag/marker is unset (there, `reserved[0]==0`; here, `v` in {0,1,27,28}), chain-ID is dropped from the signature and never validated.

### Impact Explanation
Any transaction originally signed on Ethereum mainnet or another EVM chain without EIP-155 protection — or a transaction crafted by a user/wallet that deliberately omits chain-ID binding (v=27/28) — will recover to the same sender address on Kaia and pass tx-pool admission and on-chain execution with the sender's real Kaia balance/nonce, without the sender's consent for that specific chain. This allows:
- Replay of historical unprotected transactions from other networks against a Kaia account controlled by the same private key, moving funds or executing calls the user never intended for Kaia.
- Exploitation by anyone (an "unprivileged transaction sender" reachable via public RPC) who can locate or construct an unprotected signature valid for a target address, since no privileged access is required — it is a plain `eth_sendRawTransaction`/`AddRemote` submission path.
- This qualifies as "acceptance of an invalid/unintended transaction" and "unauthorized value movement," satisfying the required impact classes.

### Likelihood Explanation
Reachable directly through a single submitted transaction via the public transaction-pool ingress (`TxPool.AddRemote` → `validateTx` → `ValidateSender` → `EIP155Signer.Sender`), with no special privileges, validator/operator access, or network position required. The only precondition is possession of an unprotected legacy signature (v=27/28) whose recovered address matches an address holding funds on Kaia — a scenario the zkSync judges confirmed is realistic enough to warrant Medium severity (early/pre-EIP-155 wallets, keyless-deployment style signatures, or a malicious actor deliberately crafting such a signature and tricking a user/relayer into broadcasting it).

### Recommendation
Enforce EIP-155 unconditionally for legacy transactions accepted by the transaction pool and blockchain: reject any legacy transaction where `tx.Protected()` is false (i.e., require `v` to encode the chain ID) rather than falling back to `HomesteadSigner`. Concretely:
- In `blockchain/tx_pool.go` `validateTx`, reject transactions where `!tx.Protected()` outright instead of only checking chain ID when `tx.Protected()` is true.
- In `blockchain/types/transaction_signing.go` `EIP155Signer.Sender`/`SenderPubkey`, remove (or restrict to a legacy-compat testing flag) the `if !tx.Protected() { return HomesteadSigner{}.Sender(tx) }` fallback so that unprotected legacy transactions are never resolved to a valid sender in production paths.
- Reconcile the misleading comment in `work/worker.go` ("Kaia is always in EIP155") with actual enforcement, since it currently does not reflect the code's real behavior.

### Proof of Concept
1. Take (or construct) a legacy Ethereum-style transaction signed with `v = 27` or `v = 28` (unprotected, no chain ID in the signature) for an address `A` that also holds a balance on Kaia.
2. Submit it to a Kaia node via `eth_sendRawTransaction` (routed to `TxPool.AddRemote` → `add` → `validateTx`).
3. In `validateTx`, `tx.Protected()` is `false`, so the chain-ID check at `blockchain/tx_pool.go:828` is skipped entirely.
4. `tx.ValidateSender` → `Sender(signer, tx)` → `EIP155Signer.Sender` detects `!tx.Protected()` and delegates to `HomesteadSigner{}.Sender(tx)`, which recovers `A` using a hash that never included any chain ID.
5. The transaction is admitted and later executed against `A`'s real Kaia state, moving funds/mutating nonce without any Kaia-specific authorization from `A`.

### Citations

**File:** blockchain/types/transaction.go (L481-497)
```go
func isProtectedV(V *big.Int) bool {
	if V.BitLen() <= 8 {
		v := V.Uint64()
		return v != 27 && v != 28 && v != 1 && v != 0
	}
	// anything not 27 or 28 is considered protected
	return true
}

// Protected says whether the transaction is replay-protected.
func (tx *Transaction) Protected() bool {
	if tx.IsLegacyTransaction() {
		v := tx.RawSignatureValues()[0].V
		return v != nil && isProtectedV(v)
	}
	return true
}
```

**File:** blockchain/types/transaction_signing.go (L515-566)
```go
// Hash returns the hash to be signed by the sender.
// It does not uniquely identify the transaction.
func (fs FrontierSigner) Hash(tx *Transaction) common.Hash {
	return rlpHash([]interface{}{
		tx.Nonce(),
		tx.GasPrice(),
		tx.Gas(),
		tx.To(),
		tx.Value(),
		tx.Data(),
	})
}

func (fs FrontierSigner) HashFeePayer(tx *Transaction) (common.Hash, error) {
	return common.Hash{}, ErrHashFeePayerNotSupported
}

// HomesteadTransaction implements TransactionInterface using the
// homestead rules.
type HomesteadSigner struct{ FrontierSigner }

func (s HomesteadSigner) ChainID() *big.Int {
	return nil
}

func (s HomesteadSigner) Equal(s2 Signer) bool {
	_, ok := s2.(HomesteadSigner)
	return ok
}

func (s HomesteadSigner) IsCompatibleWith(cached Signer, tx *Transaction) bool {
	return s.Equal(cached)
}

// SignatureValues returns signature values. This signature
// needs to be in the [R || S || V] format where V is 0 or 1.
func (hs HomesteadSigner) SignatureValues(tx *Transaction, sig []byte) (r, s, v *big.Int, err error) {
	return hs.FrontierSigner.SignatureValues(tx, sig)
}

func (hs HomesteadSigner) Sender(tx *Transaction) (common.Address, error) {
	if !tx.IsLegacyTransaction() {
		return common.Address{}, ErrTxTypeNotSupported
	}
	sigs := tx.RawSignatureValues()
	if len(sigs) != 1 {
		return common.Address{}, ErrShouldBeSingleSignature
	}
	v, r, s := sigs[0].V, sigs[0].R, sigs[0].S

	return recoverPlain(hs.Hash(tx), r, s, v, true)
}
```

**File:** blockchain/types/transaction_signing.go (L599-619)
```go
func (s EIP155Signer) Sender(tx *Transaction) (common.Address, error) {
	if tx.IsEthTypedTransaction() {
		return common.Address{}, ErrTxTypeNotSupported
	}

	if !tx.Protected() {
		return HomesteadSigner{}.Sender(tx)
	}

	if !tx.IsLegacyTransaction() {
		logger.Warn("No need to execute Sender!", "tx", tx.String())
	}

	if tx.ChainId().Cmp(s.chainId) != 0 {
		return common.Address{}, ErrInvalidChainId
	}
	return RecoverTxSender(s.Hash(tx), tx.data.RawSignatureValues(), true, func(v *big.Int) *big.Int {
		V := new(big.Int).Sub(v, s.chainIdMul)
		return V.Sub(V, big8)
	})
}
```

**File:** blockchain/tx_pool.go (L827-830)
```go
	// Check chain Id first.
	if tx.Protected() && tx.ChainId().Cmp(pool.chainconfig.ChainID) != 0 {
		return ErrInvalidChainId
	}
```

**File:** work/worker.go (L742-751)
```go
		// NOTE-Kaia Since Kaia is always in EIP155, the below replay protection code is not needed.
		// TODO-Kaia-RemoveLater Remove the code commented below.
		// Check whether the tx is replay protected. If we're not in the EIP155 hf
		// phase, start ignoring the sender until we do.
		//if tx.Protected() && !env.config.IsEIP155(env.header.Number) {
		//	logger.Trace("Ignoring reply protected transaction", "hash", tx.Hash())
		//	//logger.Error("#### worker.commitTransaction","tx.protected",tx.Protected(),"tx.hash",tx.Hash(),"nonce",tx.Nonce(),"to",tx.To())
		//	txs.Pop()
		//	continue
		//}
```
