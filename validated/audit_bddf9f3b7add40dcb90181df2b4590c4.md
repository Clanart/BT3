## Finding: Fee-payer co-signing RPCs skip fee-cap validation and silently substitute live gas parameters before signing

### Title
Kaia-native `SignTransactionAsFeePayer`/`SignTransaction` RPCs sign transactions with silently populated gas parameters and no fee-cap check - ([File: api/api_kaia_transaction.go])

### Summary
The Uniswap report describes a bug class where a wallet silently populates transaction fields (gas price, gas limit, nonce, destination) from an untrusted/remote source and signs the result without giving the signer a chance to review the final values. Kaia's own fee-delegation signing RPCs, `KaiaTransactionAPI.SignTransactionAsFeePayer` and `PersonalAPI.SignTransactionAsFeePayer`, exhibit the same root cause: they call `args.setDefaults()` — which pulls live network suggestions for nonce/gas price/fee caps — and then immediately sign, with no fee-sanity check equivalent to the one used in the Ethereum-compatible signing path.

### Finding Description
`KaiaTransactionAPI.SignTransactionAsFeePayer` unconditionally calls `args.setDefaults(ctx, s.b)` before building and signing the transaction as fee payer: [1](#0-0) 

`setDefaults` (shared helper) fills in `Nonce`, `GasLimit`, `Price`/`MaxFeePerGas` using current chain-suggested values whenever the caller leaves them unset: [2](#0-1) 

The sibling broadcast method `SendTransactionAsFeePayer` explicitly guards against this by rejecting requests that omit `TypeInt`, `AccountNonce`, `GasLimit`, or `Price`, with the comment stating the exact concern: "Don't allow dynamic assign of values from the setDefaults function since the sender already signed on specific values": [3](#0-2) 

However, `SignTransactionAsFeePayer` (and its `PersonalAPI` counterpart) has none of these guards, and additionally tolerates a `nil` sender signature by design ("Don't return errors for nil signature allowing the fee payer to sign a tx earlier than the sender"): [4](#0-3) 

Unlike the Ethereum-compatible `EthAPI.SignTransaction`, which explicitly enforces `checkTxFee(tx.GasPrice(), tx.Gas(), b.RPCTxFeeCap())` before signing: [5](#0-4) 

there is no equivalent `checkTxFee`/`RPCTxFeeCap` usage anywhere in `api/api_kaia_transaction.go` or `api/api_personal.go` — confirmed by search showing `RPCTxFeeCap`/`checkTxFee` referenced only in `api/api_eth.go`, `node/cn/api_backend.go`, `node/cn/config.go`, `api/backend.go`, and `cmd/utils/config.go`, never in the fee-payer signing paths.

### Impact Explanation
A fee-delegation counterparty (sender) calling the fee-payer node's `kaia_signTransactionAsFeePayer` / `personal_signTransactionAsFeePayer` RPC can:
1. Omit `Nonce`/`GasLimit`/`Price` and/or `TxSignatures`, letting `setDefaults` silently fill in the fields the fee payer will ultimately be bound to pay for.
2. Alternatively, explicitly supply an arbitrarily large `GasLimit`/`Price` combination — since no `checkTxFee`/fee-cap check exists on this path, unlike `EthAPI.SignTransaction`.
3. Receive back a valid fee-payer signature over a transaction whose sender-signature field is not yet bound/verified, then pair it with a sender signature they fully control (since `to`/`value`/`data`/gas fields were never authorized against any independent, pre-agreed value) and broadcast via `SendRawTransaction`.

This allows fee-delegation abuse / unauthorized value movement against the fee payer's account, draining fee payer funds beyond what any reasonable fee cap would allow, because the RPC signs whatever gas parameters end up in the final `args.toTransaction()` output without validating them against a fee ceiling or against out-of-band-agreed values.

### Likelihood Explanation
Exploitability depends on whether an operator exposes `SignTransactionAsFeePayer` as a relay/gas-station RPC to third-party senders (a legitimate, documented fee-delegation deployment pattern). Given the codebase's own comment on `SendTransactionAsFeePayer` acknowledging this exact class of risk and mitigating it there, but not on the `Sign`-only variant, this is a real inconsistency reachable by any authorized fee-delegation counterparty of such a service, without requiring a compromised peer, validator, or node.

### Recommendation
- Apply the same guard used in `SendTransactionAsFeePayer` (require `TypeInt`, `AccountNonce`, `GasLimit`, `Price`/fee fields to be explicitly specified, disallowing `setDefaults` to silently populate fee-affecting fields) to `SignTransactionAsFeePayer` in both `api/api_kaia_transaction.go` and `api/api_personal.go`.
- Add a `checkTxFee(tx.GasPrice(), tx.Gas(), b.RPCTxFeeCap())`-equivalent check before the fee payer signs, mirroring `EthAPI.SignTransaction`'s behavior in `api/api_eth.go`.
- Require verification that any supplied `args.TxSignatures` actually validates against the fully-populated transaction fields before the fee payer signs, rather than accepting a `nil` or unchecked sender signature.

### Proof of Concept
1. Operator runs a Kaia node exposing `kaia_signTransactionAsFeePayer` (or `personal_signTransactionAsFeePayer`) as a fee-delegation relay service, with an unlocked fee-payer account.
2. Attacker (fee-delegation counterparty) calls `kaia_signTransactionAsFeePayer` with `From = feePayer`, `To/Value/Data` of their choosing, omitting `Nonce`/`GasLimit`/`Price`, and `TxSignatures = nil`.
3. `SignTransactionAsFeePayer` → `args.setDefaults()` fills current suggested gas price/limit/nonce → `toTransaction()` builds the tx → fee payer signs it (`s.signAsFeePayer`), returning `feePayerSignedTx`.
4. Attacker signs the exact same fully-populated transaction as sender (they control every field), producing a fully valid fee-delegated transaction, and submits it via `SendRawTransaction`.
5. The fee payer's account pays gas for a transaction whose value/fee parameters were never independently reviewed or capped by the fee payer service, demonstrating fee-delegation abuse enabled by the missing checks identified above.

### Citations

**File:** api/api_kaia_transaction.go (L353-380)
```go
// SendTransactionAsFeePayer creates a transaction for the given argument, sign it as a fee payer
// and submit it to the transaction pool.
func (s *KaiaTransactionAPI) SendTransactionAsFeePayer(ctx context.Context, args SendTxArgs) (common.Hash, error) {
	// Don't allow dynamic assign of values from the setDefaults function since the sender already signed on specific values.
	if args.TypeInt == nil {
		return common.Hash{}, errTxArgNilTxType
	}
	if args.AccountNonce == nil {
		return common.Hash{}, errTxArgNilNonce
	}
	if args.GasLimit == nil {
		return common.Hash{}, errTxArgNilGas
	}
	if args.Price == nil {
		return common.Hash{}, errTxArgNilGasPrice
	}

	if args.TxSignatures == nil {
		return common.Hash{}, errTxArgNilSenderSig
	}

	feePayerSignedTx, err := s.SignTransactionAsFeePayer(ctx, args)
	if err != nil {
		return common.Hash{}, err
	}

	return submitTransaction(ctx, s.b, feePayerSignedTx.Tx)
}
```

**File:** api/api_kaia_transaction.go (L493-519)
```go
func (s *KaiaTransactionAPI) SignTransactionAsFeePayer(ctx context.Context, args SendTxArgs) (*SignTransactionResult, error) {
	// Allows setting a default nonce value of the sender just for the case the fee payer tries to sign a tx earlier than the sender.
	if err := args.setDefaults(ctx, s.b); err != nil {
		return nil, err
	}
	tx, err := args.toTransaction()
	if err != nil {
		return nil, err
	}
	// Don't return errors for nil signature allowing the fee payer to sign a tx earlier than the sender.
	if args.TxSignatures != nil {
		tx.SetSignature(args.TxSignatures.ToTxSignatures())
	}
	feePayer, err := tx.FeePayer()
	if err != nil {
		return nil, errTxArgInvalidFeePayer
	}
	feePayerSignedTx, err := s.signAsFeePayer(feePayer, tx)
	if err != nil {
		return nil, err
	}
	data, err := rlp.EncodeToBytes(feePayerSignedTx)
	if err != nil {
		return nil, err
	}
	return &SignTransactionResult{data, feePayerSignedTx}, nil
}
```

**File:** api/tx_args.go (L148-218)
```go
// setDefaults is a helper function that fills in default values for unspecified common tx fields.
func (args *SendTxArgs) setDefaults(ctx context.Context, b Backend) error {
	isMagma := b.ChainConfig().IsMagmaForkEnabled(new(big.Int).Add(b.CurrentBlock().Number(), big.NewInt(1)))

	if args.TypeInt == nil {
		args.TypeInt = new(types.TxType)
		*args.TypeInt = types.TxTypeLegacyTransaction
	}
	if args.GasLimit == nil {
		args.GasLimit = new(hexutil.Uint64)
		*args.GasLimit = hexutil.Uint64(90000)
	}
	// Eth typed transactions requires chainId.
	if args.TypeInt.IsEthTypedTransaction() {
		if args.ChainID == nil {
			args.ChainID = (*hexutil.Big)(b.ChainConfig().ChainID)
		}
	}

	// b.SuggestTipCap = unitPrice  		for before Magma
	//                 = zero				for after Magma
	//                 = tipFromFeeHistory  for after Kaia
	tip, err := b.SuggestTipCap(ctx)
	if err != nil {
		return err
	}

	// b.SuggestPrice = unitPrice, for before Magma
	//                = baseFee * 2,   for after Magma
	//                = baseFee + tip  for after Kaia
	price, err := b.SuggestPrice(ctx)
	if err != nil {
		return err
	}

	// For the transaction that do not use the gasPrice field, the default value of gasPrice is not set.
	if args.Price == nil && (*args.TypeInt != types.TxTypeEthereumDynamicFee && *args.TypeInt != types.TxTypeEthereumSetCode) {
		args.Price = (*hexutil.Big)(price)
	}

	if *args.TypeInt == types.TxTypeEthereumDynamicFee || *args.TypeInt == types.TxTypeEthereumSetCode {
		if args.MaxPriorityFeePerGas == nil {
			args.MaxPriorityFeePerGas = (*hexutil.Big)(tip)
		}
		if args.MaxFeePerGas == nil {
			// Before Magma hard fork, `gasFeeCap` was set to `baseFee*2 + maxPriorityFeePerGas` by default.
			gasFeeCap := new(big.Int).Set((*big.Int)(args.MaxPriorityFeePerGas))
			if isMagma {
				// After Magma hard fork, `gasFeeCap` was set to `baseFee*2` by default.
				gasFeeCap = price
			}
			args.MaxFeePerGas = (*hexutil.Big)(gasFeeCap)
		}
		if isMagma {
			if args.MaxFeePerGas.ToInt().Cmp(new(big.Int).Div(price, common.Big2)) < 0 {
				return fmt.Errorf("maxFeePerGas (%v) < BaseFee (%v)", args.MaxFeePerGas, price)
			}
		} else if args.MaxPriorityFeePerGas.ToInt().Cmp(price) != 0 || args.MaxFeePerGas.ToInt().Cmp(price) != 0 {
			return fmt.Errorf("only %s is allowed to be used as maxFeePerGas and maxPriorityPerGas", price.Text(16))
		}
		if args.MaxFeePerGas.ToInt().Cmp(args.MaxPriorityFeePerGas.ToInt()) < 0 {
			return fmt.Errorf("maxFeePerGas (%v) < maxPriorityFeePerGas (%v)", args.MaxFeePerGas, args.MaxPriorityFeePerGas)
		}
	}
	if args.AccountNonce == nil {
		nonce := b.GetPoolNonce(ctx, args.From)
		args.AccountNonce = (*hexutil.Uint64)(&nonce)
	}

	return nil
}
```

**File:** api/api_personal.go (L376-406)
```go
// SignTransactionAsFeePayer will create a transaction from the given arguments and
// try to sign it as a fee payer with the key associated with args.From. If the given
// password isn't able to decrypt the key, it fails. The transaction is returned in RLP-form,
// not broadcast to other nodes
func (s *PersonalAPI) SignTransactionAsFeePayer(ctx context.Context, args SendTxArgs, passwd string) (*SignTransactionResult, error) {
	// Allows setting a default nonce value of the sender just for the case the fee payer tries to sign a tx earlier than the sender.
	if err := args.setDefaults(ctx, s.b); err != nil {
		return nil, err
	}
	tx, err := args.toTransaction()
	if err != nil {
		return nil, err
	}
	// Don't return errors for nil signature allowing the fee payer to sign a tx earlier than the sender.
	if args.TxSignatures != nil {
		tx.SetSignature(args.TxSignatures.ToTxSignatures())
	}
	feePayer, err := tx.FeePayer()
	if err != nil {
		return nil, errTxArgInvalidFeePayer
	}
	feePayerSignedTx, err := s.signAsFeePayer(feePayer, passwd, tx)
	if err != nil {
		return nil, err
	}
	data, err := rlp.EncodeToBytes(feePayerSignedTx)
	if err != nil {
		return nil, err
	}
	return &SignTransactionResult{data, feePayerSignedTx}, nil
}
```

**File:** api/api_eth.go (L1150-1170)
```go
func (api *EthAPI) SignTransaction(ctx context.Context, args EthTransactionArgs) (*EthSignTransactionResult, error) {
	b := api.kaiaTransactionAPI.b

	if args.Gas == nil {
		return nil, fmt.Errorf("gas not specified")
	}
	if args.GasPrice == nil && (args.MaxPriorityFeePerGas == nil || args.MaxFeePerGas == nil) {
		return nil, fmt.Errorf("missing gasPrice or maxFeePerGas/maxPriorityFeePerGas")
	}
	if args.Nonce == nil {
		return nil, fmt.Errorf("nonce not specified")
	}
	if err := args.setDefaults(ctx, b); err != nil {
		return nil, err
	}
	// Before actually sign the transaction, ensure the transaction fee is reasonable.
	tx, _ := args.toTransaction()
	if err := checkTxFee(tx.GasPrice(), tx.Gas(), b.RPCTxFeeCap()); err != nil {
		return nil, err
	}
	signed, err := api.kaiaTransactionAPI.sign(args.from(), tx)
```
