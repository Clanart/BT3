## Title
Nil-pointer panic when processing an EIP-7702 `SetCodeTx` with an empty `To` field crashes the node during ante-handling — (`File: x/evm/types/ethtx/set_code_tx.go`)

### Summary
`SetCodeTx.GetTo()` returns `nil` when the proto `To` field is an empty string, but `SetCodeTx.AsEthereumData()` unconditionally dereferences that pointer (`To: *tx.GetTo()`), so any submitted `MsgEVMTransaction` carrying a `SetCodeTx` payload with `To == ""` will pass `ValidateBasic()`/`Validate()` and then panic when the ante pipeline converts it to an `ethtypes.Transaction`. This mirrors the Zebra bug class: a field that is lazily/incompletely validated during deserialization is eagerly (and unsafely) assumed to be present when the transaction is later converted for signature/hash processing.

### Finding Description
`SetCodeTx.GetTo()`: [1](#0-0) 
returns `nil` for an empty `To`, whereas `AsEthereumData()` dereferences it unconditionally: [2](#0-1) 

`Validate()` only checks the address format *if* `To != ""`, it never requires `To` to be non-empty for a `SetCodeTx`: [3](#0-2) 

`MsgEVMTransaction.ValidateBasic()` calls exactly that `Validate()` and nothing more for non-`AssociateTx` payloads, so a `SetCodeTx` with an empty `To` passes: [4](#0-3) 

The unsafe conversion is reached from multiple ante/CheckTx paths that every default-configuration node executes for every submitted transaction:
- `EvmStatelessChecks` (used by `EvmCheckTxAnte` and by the giga executor's `gigaDeliverTx`) calls `msg.AsTransaction()`, which calls `AsEthereumData()`: [5](#0-4) [6](#0-5) 
- `EvmCheckTxAnte` itself also converts directly via `CheckAndDecodeSignature`: [7](#0-6) [8](#0-7) 
- `x/evm/ante/preprocess.go`'s `PreprocessUnpacked` (the V2 ante handler's `Preprocess`, called for every EVM tx during CheckTx/DeliverTx and in `App.finalizeDecodedEVMSlot`) does the identical unsafe conversion: [9](#0-8) [10](#0-9) 

Since `EvmStatelessChecks` is invoked before broadcast/CheckTx admission (and in giga's `gigaDeliverTx`), a crafted `MsgEVMTransaction` reaches this code well before consensus-critical execution, on any node's mempool/RPC ingestion path — i.e., reachable by a plain unprivileged transaction sender via `broadcast_tx`/`CheckTx`, not requiring a malicious peer or validator.

Note: contrast with `BlobTx.GetTo()`, which defensively returns `&common.Address{}` for an empty `To` instead of `nil`: [11](#0-10) 
`SetCodeTx.GetTo()` lacks this same defensive fallback, which is the root cause of the discrepancy.

### Impact Explanation
A panic during `CheckTx`/ante processing that is not recovered crashes the node process (or, if `gigaDeliverTx`'s `recover()` catches it in the deliver path, results in an `ErrPanic` response — but `EvmStatelessChecks`/`EvmCheckTxAnte` invoked from `CheckTx` outside the giga recover wrapper is a plain function call in the standard ABCI `CheckTx` handler). A panic that escapes CheckTx crashes the validating/RPC node's process — satisfying the "crash of default-configuration RPC nodes" criterion. Because `EvmStatelessChecks`/`Preprocess` run on every node that ingests the transaction (both for mempool admission and block processing), this is a network-wide, remotely triggerable denial of service requiring only a single crafted transaction, with no special privileges.

### Likelihood Explanation
High. `SetCodeTx` (EIP-7702) is a first-class, publicly reachable transaction type (`AllowedTxTypes` for `derived.Prague`) that any external RPC client can submit as a `MsgEVMTransaction`. Producing an Any-packed `SetCodeTx` with an omitted/empty `To` field requires no special access, just crafting the protobuf payload directly (bypassing the go-ethereum RLP encoder's own field-presence enforcement, which the Cosmos-native `ethtx.SetCodeTx` proto type does not replicate).

### Recommendation
In `x/evm/types/ethtx/set_code_tx.go`:
1. Make `GetTo()` fail-safe like `BlobTx.GetTo()` (return `&common.Address{}` rather than `nil`) — OR —
2. Add an explicit `To != ""` (or address-format) requirement inside `Validate()` for `SetCodeTx` (EIP-7702 requires a non-nil `To`; contract-creation SetCode transactions are invalid per EIP-7702) so malformed messages are rejected before ever reaching `AsEthereumData()`.
3. Defensively guard the dereference in `AsEthereumData()` itself (`if to := tx.GetTo(); to != nil { ... }`), so future callers of `GetTo()` cannot reintroduce this nil-pointer panic.

### Proof of Concept
1. Construct a `SetCodeTx` protobuf message with `To` left as the zero value (`""`), valid `GasTipCap`/`GasFeeCap` (non-negative, `GasFeeCap >= GasTipCap`), a non-nil `ChainID`, and syntactically valid 32-byte `V`/`R`/`S` values so that `SetCodeTx.Validate()` passes.
2. Pack it into a `MsgEVMTransaction.Data` `Any` field (as done by `PackTxData`), wrap in a Cosmos `Tx` with exactly one message, and submit it via `broadcast_tx_sync`/`broadcast_tx_commit` (or `eth_sendRawTransaction` if a bridging path constructs the same message type).
3. `EvmStatelessChecks`/`EvmCheckTxAnte`/`Preprocess` calls `msg.AsTransaction()`/`AsEthereumData()`, which executes `To: *tx.GetTo()` where `tx.GetTo()` returns `nil` (since `tx.To == ""`), causing a nil-pointer dereference panic in the node's CheckTx/ante path.

### Citations

**File:** x/evm/types/ethtx/set_code_tx.go (L117-123)
```go
func (tx *SetCodeTx) GetTo() *common.Address {
	if tx.To == "" {
		return nil
	}
	to := common.HexToAddress(tx.To)
	return &to
}
```

**File:** x/evm/types/ethtx/set_code_tx.go (L125-142)
```go
func (tx *SetCodeTx) AsEthereumData() ethtypes.TxData {
	v, r, s := tx.GetRawSignatureValues()
	return &ethtypes.SetCodeTx{
		ChainID:    bigToUint256(tx.GetChainID()),
		Nonce:      tx.GetNonce(),
		GasTipCap:  bigToUint256(tx.GetGasTipCap()),
		GasFeeCap:  bigToUint256(tx.GetGasFeeCap()),
		Gas:        tx.GetGas(),
		To:         *tx.GetTo(),
		Value:      bigToUint256(tx.GetValue()),
		Data:       tx.GetData(),
		AccessList: tx.GetAccessList(),
		AuthList:   tx.GetAuthList(),
		V:          bigToUint256(v),
		R:          bigToUint256(r),
		S:          bigToUint256(s),
	}
}
```

**File:** x/evm/types/ethtx/set_code_tx.go (L181-219)
```go
func (tx SetCodeTx) Validate() error {
	if tx.GasTipCap == nil {
		return errors.New("gas tip cap cannot nil")
	}

	if tx.GasFeeCap == nil {
		return errors.New("gas fee cap cannot nil")
	}

	if tx.GasTipCap.IsNegative() {
		return fmt.Errorf("gas tip cap cannot be negative %s", tx.GasTipCap)
	}

	if tx.GasFeeCap.IsNegative() {
		return fmt.Errorf("gas fee cap cannot be negative %s", tx.GasFeeCap)
	}

	if tx.GasFeeCap.LT(*tx.GasTipCap) {
		return fmt.Errorf("max priority fee per gas higher than max fee per gas (%s > %s)",
			tx.GasTipCap, tx.GasFeeCap,
		)
	}

	if !IsValidInt256(tx.Fee()) {
		return errors.New("fee out of bound")
	}

	amount := tx.GetValue()
	// Amount can be 0
	if amount != nil && amount.Sign() == -1 {
		return fmt.Errorf("amount cannot be negative %s", amount)
	}

	if tx.To != "" {
		if err := ValidateAddress(tx.To); err != nil {
			return errors.New("invalid to address")
		}
	}

```

**File:** x/evm/types/message_evm_transaction.go (L44-56)
```go
func (msg *MsgEVMTransaction) ValidateBasic() error {
	if msg.Derived != nil && msg.Derived.PubKey == nil {
		return sdkerrors.ErrInvalidPubKey
	}
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return err
	}
	if _, ok := txData.(*ethtx.AssociateTx); !ok {
		if err := txData.Validate(); err != nil {
			return err
		}
	}
```

**File:** x/evm/types/message_evm_transaction.go (L67-74)
```go
func (msg *MsgEVMTransaction) AsTransaction() (*ethtypes.Transaction, ethtx.TxData) {
	txData, err := UnpackTxData(msg.Data)
	if err != nil {
		return nil, nil
	}

	return ethtypes.NewTx(txData.AsEthereumData()), txData
}
```

**File:** app/ante/evm_checktx.go (L52-52)
```go
	etx := ethtypes.NewTx(txData.AsEthereumData())
```

**File:** app/ante/evm_checktx.go (L97-107)
```go
	txData, err := evmtypes.UnpackTxData(msg.Data)
	if err != nil {
		return err
	}
	if _, ok := txData.(*ethtx.AssociateTx); ok {
		return nil
	}
	etx, _ := msg.AsTransaction()
	if etx.To() == nil && len(etx.Data()) > params.MaxInitCodeSize {
		return fmt.Errorf("%w: code size %v, limit %v", core.ErrMaxInitCodeSizeExceeded, len(etx.Data()), params.MaxInitCodeSize)
	}
```

**File:** app/ante/evm_checktx.go (L206-207)
```go
func CheckAndDecodeSignature(ctx sdk.Context, txData ethtx.TxData, chainID *big.Int, isBlockTest bool) (common.Address, sdk.AccAddress, cryptotypes.PubKey, derived.SignerVersion, error) {
	ethTx := ethtypes.NewTx(txData.AsEthereumData())
```

**File:** x/evm/ante/preprocess.go (L208-208)
```go
	ethTx := ethtypes.NewTx(txData.AsEthereumData())
```

**File:** app/app.go (L2229-2243)
```go
// finalizeDecodedEVMSlot runs EVM Preprocess for a decoded tx at idx. typedTx must be non-nil EVM.
// Panics and preprocess errors clear typedTxs[idx].
func (app *App) finalizeDecodedEVMSlot(ctx sdk.Context, idx int, typedTx sdk.Tx, typedTxs []sdk.Tx) {
	defer func() {
		if err := recover(); err != nil {
			logger.Error("encountered panic during transaction preprocessing", "err", err)
			typedTxs[idx] = nil
		}
	}()
	msg := evmtypes.MustGetEVMTransactionMessage(typedTx)
	if err := evmante.Preprocess(ctx, msg, app.EvmKeeper.ChainID(ctx), app.EvmKeeper.EthBlockTestConfig.Enabled); err != nil {
		logger.Error("error preprocessing EVM tx", "err", err)
		typedTxs[idx] = nil
	}
}
```

**File:** x/evm/types/ethtx/blob_tx.go (L128-134)
```go
func (tx *BlobTx) GetTo() *common.Address {
	if tx.To == "" {
		return &common.Address{}
	}
	to := common.HexToAddress(tx.To)
	return &to
}
```
