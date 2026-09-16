### Title
Unbounded per-transaction signature count lets an unprivileged sender force uncapped ECDSA-recovery cost before any cheap rejection check runs - (File: `blockchain/types/tx_signatures.go`, `blockchain/types/transaction.go`, `blockchain/tx_pool.go`)

### Summary
Kaia's tx-pool admission path (`TxPool.validateTx`) calls `tx.ValidateSender()`, which recovers a public key for *every* signature attached to a Kaia-typed transaction via `TxSignatures.RecoverPubkey`, before the number of signatures is checked against the sender account's configured key-count limit (`MaxNumKeysForMultiSig`) or the sender's balance is checked. This mirrors the reported Zebra pattern of "expensive verification before cheap rejection": a peer/RPC caller can submit a transaction whose signature list is large and cryptographically garbage, forcing the node to perform many costly `Ecrecover` operations under the tx-pool lock before any inexpensive, cost-bounding check (account key count, balance) executes.

### Finding Description
`TxSignatures.RecoverPubkey` iterates over the full signature slice and calls `Ecrecover` once per entry with no upper bound on `len(t)` at that point: [1](#0-0) 

`Transaction.ValidateSender` calls `SenderPubkey` (which drives `RecoverTxPubkeys`/`RecoverPubkey`) to recover *all* signatures first, and only afterwards asks the account key for `SigValidationGas`, which is where the account's actual multisig key-count cap (`MaxNumKeysForMultiSig`) is enforced: [2](#0-1) 

The cap that is supposed to bound signature-verification cost, `AccountKeyWeightedMultiSig.SigValidationGas`, only rejects `numKeys > MaxNumKeysForMultiSig` *after* the account is loaded and the corresponding gas is computed - i.e., after all the recovery work above has already been paid for: [3](#0-2) 

In the tx-pool admission function, `tx.ValidateSender` (which does the expensive per-signature `Ecrecover` loop) is invoked before the sender's balance is checked and before intrinsic-gas/fee checks: [4](#0-3) [5](#0-4) 

The only size restriction applied earlier is `MaxTxDataSize`, which bounds `tx.SizeWithoutBlobTxSidecar()` (the whole encoded transaction, including its signature list), not the *signature count* specifically: [6](#0-5) 

Because each `(v,r,s)` triple is small, a transaction sized up to `MaxTxDataSize` can carry a large number of signature tuples, all of which get individually `Ecrecover`'d - work proportional to attacker-chosen signature count, performed unconditionally by `ValidateSender`/`ValidateFeePayer` for both sender and fee-payer signature lists, before the account-specific key-count cap or the balance check ever runs. This is directly analogous to the Zebra bug: the resource-bounding check (there: ZIP-317 fee pre-check before Halo2 proof; here: account key-count cap / balance check before ECDSA recovery) runs *after*, not *before*, the expensive cryptographic operation.

### Impact Explanation
Any unprivileged remote caller (`eth_sendRawTransaction` via public RPC, or a P2P-relayed tx that reaches `TxPool.AddRemote`/`validateTx`) can submit transactions engineered to maximize the signature-list length while staying within `MaxTxDataSize`. Because `TxPool.validateTx` executes under the pool's synchronization for each transaction, and this cost is paid before the fast, cheap balance/key-count checks that would otherwise reject the transaction near-instantly, an attacker with negligible/no funded balance can force the node to spend recovery-proportional CPU on every submitted transaction. Submitted at volume, this degrades tx-pool throughput and admission latency for legitimate senders, a resource-consumption/availability impact consistent with CWE-405/CWE-770.

### Likelihood Explanation
High reachability: the path is exercised by ordinary `AddRemote`/RPC transaction submission with no authentication, no special account setup, and no valid signature required (only RLP-decodable `v/r/s` values need to pass the cheap `ValidateSignatureValues` bit-length check before `Ecrecover` is attempted). The attacker does not need a funded account, since the balance check occurs after the recovery loop.

### Recommendation
Enforce a per-transaction bound on the number of signatures (e.g., `MaxNumKeysForMultiSig`) at decode/pool-admission time, before `ValidateSender`/`ValidateFeePayer` performs any `Ecrecover`. Alternatively, reorder `TxPool.validateTx` to perform balance/fee/gas-cap checks that don't require signature recovery before invoking `ValidateSender`, and reject transactions whose signature-list length exceeds the maximum any account key type could ever require.

### Proof of Concept
1. Construct a Kaia typed transaction (e.g., `TxTypeValueTransfer`) whose `TxSignatures` field contains the maximum number of `(v, r, s)` tuples that fit under `MaxTxDataSize`, each populated with syntactically valid but cryptographically meaningless values that pass `crypto.ValidateSignatureValues` (chosen `r`, `s` in valid range, `v` in {27,28}).
2. Submit the transaction repeatedly via `eth_sendRawTransaction` (or directly to `TxPool.AddRemote`) from an account with zero balance.
3. Observe that `TxPool.validateTx` → `tx.ValidateSender` → `SenderPubkey` → `RecoverTxPubkeys` → `TxSignatures.RecoverPubkey` performs one `Ecrecover` per signature entry (proportional to attacker-chosen count) before the balance check (`blockchain/tx_pool.go:966`) or the account-key-count cap (`account_key_weighted_multi_sig.go:161-178`) ever executes and rejects the transaction.

Note: I was not able to fully confirm the exact numeric value of `MaxTxDataSize` and the resulting maximum achievable signature count within the indexed context, so the precise amplification factor (attacker-cost vs. victim-CPU-cost ratio) is unverified; a Devin session with full repository access would be needed to measure this concretely and confirm exploitability at scale.

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

**File:** blockchain/tx_pool.go (L908-989)
```go
	var (
		from          = tx.ValidatedSender()
		senderBalance = pool.getBalance(from)
		gasFeePayer   = uint64(0)
	)
	// Ensure the transaction adheres to nonce ordering
	if pool.getNonce(from) > tx.Nonce() {
		return ErrNonceTooLow
	}

	// If module recognizes the tx, run an alternative balance check and then skip the default balance check later.
	shouldSkipBalanceCheck := false
	for _, module := range pool.modules {
		if module.IsModuleTx(tx) {
			if checkBalance := module.GetCheckBalance(); checkBalance != nil {
				shouldSkipBalanceCheck = true
				err := checkBalance(tx)
				if err != nil {
					logger.Trace("[tx_pool] invalid funds of module transaction sender", "from", from, "txhash", tx.Hash().Hex())
					return err
				}
			}
			break
		}
	}

	// Transactor should have enough funds to cover the costs
	// cost == V + GP * GL
	if tx.IsFeeDelegatedTransaction() {
		// balance check for fee-delegated tx
		gasFeePayer, err = tx.ValidateFeePayer(pool.signer, pool.currentState, pool.currentBlockNumber)
		if err != nil {
			return types.ErrFeePayer(err)
		}

		var (
			feePayer            = tx.ValidatedFeePayer()
			feePayerBalance     = pool.getBalance(feePayer)
			feeRatio, isRatioTx = tx.FeeRatio()
		)
		if isRatioTx {
			// Check fee ratio range
			if !feeRatio.IsValid() {
				return kerrors.ErrFeeRatioOutOfRange
			}

			feeByFeePayer, feeBySender := types.CalcFeeWithRatio(feeRatio, tx.Fee())

			if senderBalance.Cmp(new(big.Int).Add(tx.Value(), feeBySender)) < 0 {
				logger.Trace("[tx_pool] insufficient funds for feeBySender", "from", from, "balance", senderBalance, "feeBySender", feeBySender)
				return ErrInsufficientFundsFrom
			}

			if feePayerBalance.Cmp(feeByFeePayer) < 0 {
				logger.Trace("[tx_pool] insufficient funds for feeByFeePayer", "feePayer", feePayer, "balance", feePayerBalance, "feeByFeePayer", feeByFeePayer)
				return ErrInsufficientFundsFeePayer
			}
		} else {
			if senderBalance.Cmp(tx.Value()) < 0 {
				logger.Trace("[tx_pool] insufficient funds for cost(value)", "from", from, "balance", senderBalance, "value", tx.Value())
				return ErrInsufficientFundsFrom
			}

			if feePayerBalance.Cmp(tx.Fee()) < 0 {
				logger.Trace("[tx_pool] insufficient funds for cost(gas * price)", "feePayer", feePayer, "balance", feePayerBalance, "fee", tx.Fee())
				return ErrInsufficientFundsFeePayer
			}
		}
		// additional balance check in case of sender = feepayer
		// since a single account has to bear the both cost(feepayer_cost + sender_cost),
		// it is necessary to check whether the balance is equal to the sum of the cost.
		if from == feePayer && senderBalance.Cmp(tx.Cost()) < 0 {
			logger.Trace("[tx_pool] insufficient funds for cost(gas * price + value)", "from", from, "balance", senderBalance, "cost", tx.Cost())
			return ErrInsufficientFundsFrom
		}
	} else if !shouldSkipBalanceCheck {
		// balance check for non-fee-delegated tx
		if senderBalance.Cmp(tx.Cost()) < 0 {
			logger.Trace("[tx_pool] insufficient funds for cost(gas * price + value)", "from", from, "balance", senderBalance, "cost", tx.Cost())
			return ErrInsufficientFundsFrom
		}
	}
```
