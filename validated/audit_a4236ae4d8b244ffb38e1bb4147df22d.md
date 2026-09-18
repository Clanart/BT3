### Title
Nil-pointer dereference in EIP-7702 `SetCodeTx.AsEthereumData()` via empty `To` field crashes node CheckTx/DeliverTx ante processing - (File: `x/evm/types/ethtx/set_code_tx.go`)

### Summary
`SetCodeTx.Validate()` does not require the `To` field to be non-empty, yet `SetCodeTx.AsEthereumData()` unconditionally dereferences the pointer returned by `GetTo()`, which is `nil` whenever `To == ""`. A `MsgEVMTransaction` carrying a directly protobuf-constructed `SetCodeTx` payload (rather than one derived from a real RLP-decoded go-ethereum transaction) with an empty `To` field passes validation and later triggers a nil-pointer dereference panic in the EVM ante pipeline that every node runs on `CheckTx`/`DeliverTx`.

### Finding Description
`GetTo()` explicitly documents/handles the "no recipient" case as returning `nil`: [1](#0-0) 

But `AsEthereumData()`, which converts the internal proto representation back into a `go-ethereum` `ethtypes.SetCodeTx`, dereferences that pointer without a nil check: [2](#0-1) 

The validation routine that is supposed to guard this invariant only checks the *format* of `To` when it is non-empty; it never requires `To` to be present at all, even though EIP-7702 set-code transactions are defined to always carry a recipient (they cannot be contract-creation transactions): [3](#0-2) 

This is the same bug class as the OpenSSL `GENERAL_NAME_cmp`/`EDIPARTYNAME` issue: an optional/nullable sub-field of an attacker-controlled structure is accepted by validation, but a *different* code path downstream (here, the ethereum-format conversion instead of a `GENERAL_NAME` comparison) assumes the field is always present and dereferences it unconditionally, causing a crash.

The normal construction path, `NewSetCodeTx(tx *ethtypes.Transaction)`, always fills `To` from a genuine decoded go-ethereum transaction, where `SetCodeTx.To` is a mandatory `common.Address` (not a pointer) — so `tx.To()` can never be empty for a real `SetCodeTx` produced via `eth_sendRawTransaction`/RLP decoding: [4](#0-3) 

However, `MsgEVMTransaction.Data` is an `Any`-packed protobuf `TxData` that can be constructed and submitted directly as a Cosmos SDK message (bypassing go-ethereum's RLP encode/decode entirely). Nothing in `UnpackTxData` or `Validate()` rejects a `SetCodeTx` proto message with `To == ""` supplied this way. Once unpacked, the ante pipeline unconditionally calls `AsEthereumData()`: [5](#0-4) 

`EvmCheckTxAnte` runs on `CheckTx` for every node that admits the transaction into its mempool (validators and public RPC/full nodes alike), unpacks the tx data, and — for anything other than an `AssociateTx` — immediately calls `ethtypes.NewTx(txData.AsEthereumData())`, which panics before any further sanity check (e.g., the "must not be contract creation" check in `app/app.go`'s giga path) can run.

### Impact Explanation
A panic thrown from `AsEthereumData()` during `CheckTx`/`DeliverTx` ante processing is not a normal error return — it is a Go runtime panic surfacing through the ABCI application's transaction-processing path. Any node (validator or public RPC node) that receives and processes this crafted `MsgEVMTransaction` will panic while handling it. Because `CheckTx` is run by every node on every incoming transaction (via gossip or direct submission), this can be broadcast network-wide, causing repeated crashes on nodes that re-process it, i.e., a denial-of-service against validators (block delay / halt) and public RPC nodes (crash of default-configuration RPC nodes).

### Likelihood Explanation
The transaction only needs to be a syntactically valid `MsgEVMTransaction` wrapping a protobuf-encoded `SetCodeTx` with an empty `To` string — a single unprivileged sender can construct and broadcast this without any special privileges, funds, or prior state setup, since `Validate()` does not reject it and no signature check occurs before `AsEthereumData()` is invoked in `EvmCheckTxAnte`'s early flow (signature/fee checks happen only after this dereference for non-associate transactions).

### Recommendation
Add an explicit `To != ""` check to `SetCodeTx.Validate()` (mirroring EIP-7702's requirement that set-code transactions must always specify a recipient and cannot be contract creations), and/or make `AsEthereumData()` defensively check `GetTo()` for `nil` and return/propagate an error instead of dereferencing it, in both `x/evm/types/ethtx/set_code_tx.go` and its `giga/deps/xevm/types/ethtx/set_code_tx.go` counterpart.

### Proof of Concept
1. Construct a `MsgEVMTransaction` whose `Data` field is a `codectypes.Any`-packed `ethtx.SetCodeTx` protobuf message with `To: ""`, valid `GasTipCap`/`GasFeeCap`/`ChainID` (to pass the rest of `Validate()`), and an empty/zero `AuthList` (or any auth list valid enough to pass `validateAuthList`).
2. Submit this as a Cosmos SDK transaction (`broadcast_tx_sync`/`broadcast_tx_async`) directly to a node's RPC endpoint — this does not require it to be RLP-encoded or pass through `eth_sendRawTransaction`.
3. On `CheckTx`, `EvmCheckTxAnte` (`app/ante/evm_checktx.go:35-56`) calls `evmtypes.UnpackTxData(msg.Data)`, which returns the crafted `*ethtx.SetCodeTx` with `To == ""`; since it is not an `AssociateTx`, the code calls `ethtypes.NewTx(txData.AsEthereumData())`, which executes `To: *tx.GetTo()` — `GetTo()` returns `nil`, and the dereference panics, crashing the node's transaction-processing goroutine/process.

Note: I was not able to fully trace whether a higher-level `recover()` in the Cosmos SDK / CometBFT ABCI harness catches this panic before it terminates the node process, or whether `UnpackTxData` itself invokes `Validate()` prior to reaching the ante handler in the exact code path shown (I confirmed `Validate()` is called inside `NewSetCodeTx`, used for RLP-derived construction, but could not fully verify whether an equivalent validation call intercepts a directly protobuf-submitted `SetCodeTx` before `AsEthereumData()` executes). This should be verified with a running node/integration test to confirm whether the panic is recovered gracefully or actually terminates the process.

### Citations

**File:** x/evm/types/ethtx/set_code_tx.go (L14-35)
```go
func NewSetCodeTx(tx *ethtypes.Transaction) (*SetCodeTx, error) {
	if err := ValidateEthTx(tx); err != nil {
		return nil, err
	}
	txData := &SetCodeTx{
		Nonce:    tx.Nonce(),
		Data:     tx.Data(),
		GasLimit: tx.Gas(),
	}
	v, r, s := tx.RawSignatureValues()
	SetConvertIfPresent(tx.To(), func(to *common.Address) string { return to.Hex() }, txData.SetTo)
	SetConvertIfPresent(tx.Value(), sdk.NewIntFromBigInt, txData.SetAmount)
	SetConvertIfPresent(tx.GasFeeCap(), sdk.NewIntFromBigInt, txData.SetGasFeeCap)
	SetConvertIfPresent(tx.GasTipCap(), sdk.NewIntFromBigInt, txData.SetGasTipCap)
	al := tx.AccessList()
	SetConvertIfPresent(&al, NewAccessList, txData.SetAccesses)
	authList := tx.SetCodeAuthorizations()
	SetConvertIfPresent(&authList, NewAuthList, txData.SetAuthList)

	txData.SetSignatureValues(tx.ChainId(), v, r, s)
	return txData, txData.Validate()
}
```

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

**File:** x/evm/types/ethtx/set_code_tx.go (L181-218)
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

**File:** app/ante/evm_checktx.go (L35-56)
```go
func EvmCheckTxAnte(
	ctx sdk.Context,
	tx sdk.Tx,
	upgradeKeeper *upgradekeeper.Keeper,
	ek *evmkeeper.Keeper,
) (returnCtx sdk.Context, returnErr error) {
	chainID := ek.ChainID(ctx)
	if err := EvmStatelessChecks(ctx, tx, chainID); err != nil {
		return ctx, err
	}
	msg := tx.GetMsgs()[0].(*evmtypes.MsgEVMTransaction)

	txData, _ := evmtypes.UnpackTxData(msg.Data) // cached and validated
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))
	if atx, ok := txData.(*ethtx.AssociateTx); ok {
		return HandleAssociateTx(ctx, ek, atx, true)
	}
	etx := ethtypes.NewTx(txData.AsEthereumData())
	evmAddr, seiAddr, seiPubkey, version, err := CheckAndDecodeSignature(ctx, txData, chainID, false)
	if err != nil {
		return ctx, err
	}
```
