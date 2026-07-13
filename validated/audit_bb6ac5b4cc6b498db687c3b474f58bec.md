### Title
EIP-712 Legacy Signed Data Omits `timeout_height`, Enabling Stale Transaction Execution After Intended Expiry - (File: `ethereum/eip712/eip712_legacy.go`)

---

### Summary

The EIP-712 legacy typed-data schema for Cosmos transactions explicitly excludes `timeout_height` from the signed fields. Because `timeout_height` is present in the JSON message payload but absent from the EIP-712 type schema, it is not committed to by the signer's signature. Any party who possesses the signed transaction bytes can freely mutate `timeout_height` — including stripping it to `0` — without invalidating the signature. This allows a transaction the user intended to expire at a specific block height to be resubmitted and executed indefinitely after that expiry.

---

### Finding Description

`LegacyWrapTxToTypedData` in `ethereum/eip712/eip712_legacy.go` defines the EIP-712 type schema for the `Tx` primary type. The schema includes `account_number`, `chain_id`, `fee`, `memo`, `msgs`, and `sequence`, but explicitly omits `timeout_height`:

```go
"Tx": {
    {Name: "account_number", Type: "string"},
    {Name: "chain_id", Type: "string"},
    {Name: "fee", Type: "Fee"},
    {Name: "memo", Type: "string"},
    {Name: "msgs", Type: "Msg[]"},
    {Name: "sequence", Type: "string"},
    // Note timeout_height was removed because it was not getting filled with the legacyTx
    // {Name: "timeout_height", Type: "string"},
},
``` [1](#0-0) 

Meanwhile, `VerifySignature` in `ante/cosmos/eip712.go` constructs `txBytes` via `legacytx.StdSignBytes`, which **does** include `tx.GetTimeoutHeight()`:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID,
    signerData.AccountNumber,
    signerData.Sequence,
    tx.GetTimeoutHeight(),
    legacytx.StdFee{Amount: tx.GetFee(), Gas: tx.GetGas()},
    msgs, tx.GetMemo(),
)
``` [2](#0-1) 

`txBytes` is then JSON-unmarshalled into `txData` and used as the EIP-712 `Message`:

```go
typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
``` [3](#0-2) 

In EIP-712, `encodeData` only hashes fields declared in the type schema. Because `timeout_height` is in the JSON message but **not** in the `"Tx"` type definition, it is silently excluded from the hash. The resulting `sigHash` is identical regardless of what value `timeout_height` carries. [4](#0-3) 

The `timeout_height` field is enforced by the Cosmos SDK's `TxTimeoutHeightDecorator`, which runs in the same ante handler chain as `LegacyEip712SigVerificationDecorator`. When `timeout_height == 0`, the decorator imposes no expiry. When it is non-zero, the transaction

### Citations

**File:** ethereum/eip712/eip712_legacy.go (L95-102)
```go
	typedData := apitypes.TypedData{
		Types:       msgTypes,
		PrimaryType: "Tx",
		Domain:      domain,
		Message:     txData,
	}

	return typedData, nil
```

**File:** ethereum/eip712/eip712_legacy.go (L129-138)
```go
		"Tx": {
			{Name: "account_number", Type: "string"},
			{Name: "chain_id", Type: "string"},
			{Name: "fee", Type: "Fee"},
			{Name: "memo", Type: "string"},
			{Name: "msgs", Type: "Msg[]"},
			{Name: "sequence", Type: "string"},
			// Note timeout_height was removed because it was not getting filled with the legacyTx
			// {Name: "timeout_height", Type: "string"},
		},
```

**File:** ante/cosmos/eip712.go (L197-207)
```go
		txBytes := legacytx.StdSignBytes( //nolint:staticcheck
			signerData.ChainID,
			signerData.AccountNumber,
			signerData.Sequence,
			tx.GetTimeoutHeight(),
			legacytx.StdFee{
				Amount: tx.GetFee(),
				Gas:    tx.GetGas(),
			},
			msgs, tx.GetMemo(),
		)
```

**File:** ante/cosmos/eip712.go (L248-251)
```go
		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
		if err != nil {
			return errorsmod.Wrap(err, "failed to create EIP-712 typed data from tx")
		}
```
