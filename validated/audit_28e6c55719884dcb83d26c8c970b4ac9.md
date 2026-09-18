Confirmed: `EVMPreprocessDecorator.associateAuthorizationAuthorities` at `x/evm/ante/preprocess.go:122-147` calls `setCodeTx.AsEthereumData()` on every EIP-7702 `SetCodeTx` message before `EVMPreprocessDecorator.AnteHandle` continues, and `SetCodeTx.Validate()` (`x/evm/types/ethtx/set_code_tx.go:181-244`) never requires `tx.To` to be non-empty, while `AsEthereumData()` unconditionally dereferences `*tx.GetTo()`.

### Title
Nil Pointer Dereference in SetCodeTx.AsEthereumData via empty `To` field crashes ante handler - ([File: x/evm/types/ethtx/set_code_tx.go])

### Summary
An EIP-7702 `SetCodeTx` (`ethtypes.SetCodeTxType`) with an empty `To` field passes `SetCodeTx.Validate()` because that function only validates `To` when non-empty, but `SetCodeTx.AsEthereumData()` unconditionally dereferences the pointer returned by `GetTo()`, which is `nil` when `To == ""`. This causes a `nil pointer dereference` panic (Go's SIGSEGV-class runtime panic), matching the bug class described in CVE-2020-25465 (null-pointer dereference on malformed input causing a denial of service).

### Finding Description
`SetCodeTx.GetTo()` explicitly returns `nil` for empty `To`: [1](#0-0) 

`SetCodeTx.AsEthereumData()` dereferences that nil pointer without a nil check: [2](#0-1) 

`SetCodeTx.Validate()` only validates `To` conditionally when non-empty, never rejecting an empty `To`: [3](#0-2) 

This is reachable by an unprivileged EVM tx sender: `EVMPreprocessDecorator.AnteHandle` runs `Preprocess`/`PreprocessUnpacked` for every incoming EVM transaction, and additionally calls `associateAuthorizationAuthorities`, which unpacks the tx data, checks for `*ethtx.SetCodeTx`, and directly calls `setCodeTx.AsEthereumData()`: [4](#0-3) 

Since `MsgEVMTransaction.ValidateBasic()` only calls `txData.Validate()` (which accepts empty `To`), a crafted `MsgEVMTransaction` wrapping a `SetCodeTx` with `To == ""` will pass validation and reach the ante handler, where the nil dereference panics.

### Impact Explanation
This ante-handler code path executes during normal transaction processing (`CheckTx`/`DeliverTx`/`ProcessProposal`/`FinalizeBlock`), not inside the giga-executor's per-tx panic-recovery wrapper shown in `app/app.go` (that recovery wraps `executeEVMTxWithGigaExecutor`, not the base SDK ante-handler pipeline used by the legacy v2 path). A panic escaping unguarded ante-handler execution during block processing/proposal validation can crash the node process or, if only some validators crash while others (running a different code path or version) do not, could produce differing behavior across validators — a potential chain halt or block-processing delay condition, which is explicitly in scope per the validation criteria ("validator halt", "block delay beyond 2.5 seconds", "crash of default-configuration RPC nodes").

### Likelihood Explanation
High likelihood of triggering the panic: any address can submit a `MsgEVMTransaction` wrapping a `SetCodeTx` (EIP-7702, type `0x04`) with `To` omitted/empty. No special privileges, funds, or association are required to reach the vulnerable code path in `associateAuthorizationAuthorities`, since it executes unconditionally for every `*ethtx.SetCodeTx` before other checks in that function.

### Recommendation
Add an explicit nil check on the pointer returned by `GetTo()` before dereferencing it in `SetCodeTx.AsEthereumData()`, and/or enforce in `SetCodeTx.Validate()` that `To` must be a valid non-empty address for `SetCodeTx` (matching EIP-7702's requirement that a SetCode transaction always has a non-nil `To`), so malformed transactions are rejected during validation rather than causing a panic deep in ante-handler logic. Additionally, wrap the ante-handler pipeline (or at minimum `associateAuthorizationAuthorities`) with panic recovery consistent with the pattern already used in `app/app.go`'s giga executor.

### Proof of Concept
1. Construct a `MsgEVMTransaction` whose `Data` is a packed `SetCodeTx` with `GasTipCap`/`GasFeeCap` set (non-nil, non-negative, `GasFeeCap >= GasTipCap`), `ChainID` set, a valid non-empty `AuthList`, valid `V/R/S` signature bytes, and `To == ""`.
2. Submit the tx via the normal EVM tx broadcast path (e.g., `eth_sendRawTransaction` on the public JSON-RPC, or directly as a Cosmos tx containing `MsgEVMTransaction`).
3. `MsgEVMTransaction.ValidateBasic()` calls `SetCodeTx.Validate()`, which does not reject the empty `To`, so the tx passes basic validation.
4. During ante-handling, `EVMPreprocessDecorator.AnteHandle` → `associateAuthorizationAuthorities` unpacks the tx data, matches `*ethtx.SetCodeTx`, and calls `setCodeTx.AsEthereumData()`, which executes `To: *tx.GetTo()` where `GetTo()` returns `nil`, causing an immediate nil pointer dereference panic.

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

**File:** x/evm/types/ethtx/set_code_tx.go (L214-218)
```go
	if tx.To != "" {
		if err := ValidateAddress(tx.To); err != nil {
			return errors.New("invalid to address")
		}
	}
```

**File:** x/evm/ante/preprocess.go (L122-132)
```go
func (p *EVMPreprocessDecorator) associateAuthorizationAuthorities(ctx sdk.Context, msg *evmtypes.MsgEVMTransaction, associateHelper *helpers.AssociationHelper) {
	txData, err := evmtypes.UnpackTxData(msg.Data)
	if err != nil {
		return
	}
	setCodeTx, ok := txData.(*ethtx.SetCodeTx)
	if !ok {
		// Only SetCode (EIP-7702) transactions carry authorizations.
		return
	}
	ethTx := ethtypes.NewTx(setCodeTx.AsEthereumData())
```
