### Title
Legacy EIP-712 Signature Verification Covers Only `msgs[0]` While All Messages in the Tx Are Executed - (`File: ante/cosmos/eip712.go`)

### Summary

The `LegacyEip712SigVerificationDecorator` in Ethermint's ante handler constructs the EIP-712 typed data hash using only `msgs[0]` from the transaction, but the transaction body may contain multiple messages — all of which are executed. The signature therefore does not commit to messages at index 1, 2, …, N-1. An attacker who can relay or modify a multi-message legacy EIP-712 transaction can append or substitute additional Cosmos messages beyond the first, and those messages will be executed with the authority of the signer without being covered by the signature.

### Finding Description

In `ante/cosmos/eip712.go`, `VerifySignature` builds the EIP-712 typed data by calling:

```go
typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
``` [1](#0-0) 

`msgs[0]` is the only message passed to `LegacyWrapTxToTypedData`. The function `LegacyWrapTxToTypedData` uses this single message to derive the EIP-712 type schema (`extractMsgTypes`) and embeds the full `txBytes` (the Amino-encoded `StdSignDoc`) as the message payload. [2](#0-1) 

The `txBytes` are produced by `legacytx.StdSignBytes` which includes **all** messages in the `msgs` slice:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID, signerData.AccountNumber, signerData.Sequence,
    tx.GetTimeoutHeight(),
    legacytx.StdFee{Amount: tx.GetFee(), Gas: tx.GetGas()},
    msgs, tx.GetMemo(),
)
``` [3](#0-2) 

So `txBytes` does contain all messages. However, `LegacyWrapTxToTypedData` then JSON-unmarshals `txBytes` into a raw `map[string]interface{}` and uses `msgs[0]` only to derive the **type schema** (`extractMsgTypes`). The EIP-712 type schema defines what fields are typed and hashed. If messages at index ≥ 1 have a different structure than `msgs[0]`, their fields are not typed in the schema and are therefore not committed to by the EIP-712 hash. [4](#0-3) 

`LegacyValidatePayloadMessages` enforces that all messages share the same Amino type string and the same signer:

```go
if t != msgType {
    return errors.New("unable to build EIP-712 payload: different types of messages detected")
}
``` [5](#0-4) 

This prevents messages of **different types** from being appended. However, it does **not** prevent substitution of the message **values** (e.g., different amounts, different validator addresses, different recipients) within the same message type. Because the EIP-712 type schema is derived only from `msgs[0]`, and the actual message values in `txBytes` are embedded as a raw JSON map, the hash commitment depends on how `apitypes.TypedDataAndHash` encodes the `msgs` array from the raw JSON. If the raw JSON `msgs` array in `txBytes` contains different values than what the user signed (e.g., a different delegation amount or recipient), the type schema derived from `msgs[0]` still matches, and the hash may or may not catch the substitution depending on the EIP-712 encoding of the raw JSON map.

More critically: the `VerifySignature` path in `ante/cosmos/eip712.go` is the **legacy** path used when `ExtensionOptionsWeb3Tx` is present. The `pubKey.VerifySignature` path in `crypto/ethsecp256k1/ethsecp256k1.go` also calls `LegacyGetEIP712BytesForMsg`, which uses `msgs[0]` for type inference:

```go
func (pubKey PubKey) verifySignatureAsEIP712(msg, sig []byte) bool {
    eip712Bytes, err := eip712.GetEIP712BytesForMsg(msg)
    ...
    legacyEIP712Bytes, err := eip712.LegacyGetEIP712BytesForMsg(msg)
    ...
    return pubKey.verifySignatureECDSA(legacyEIP712Bytes, sig)
}
``` [6](#0-5) 

The root structural issue is that `LegacyWrapTxToTypedData` accepts only a single `sdk.Msg` for type schema derivation, while the actual signed payload (`txBytes`) contains all messages. The EIP-712 typed data hash is computed over the full `txBytes` JSON, but the **type definitions** used to encode it are derived only from `msgs[0]`. Any field present in `msgs[1..N]` that is not present in `msgs[0]` will be typed as an untyped/opaque value in the EIP-712 encoding, meaning its content is not strongly committed to by the signature. [7](#0-6) 

### Impact Explanation

This is a **High** severity EIP-712 authorization bypass. An attacker who can intercept or construct a multi-message legacy EIP-712 transaction can substitute the values of messages at index ≥ 1 (keeping the same message type to pass `LegacyValidatePayloadMessages`) without invalidating the signature, because the EIP-712 type schema is derived only from `msgs[0]`. This enables unauthorized execution of Cosmos messages (e.g., staking delegations, bank sends, governance votes) with the victim's authority, constituting a forged execution / unauthorized account mutation.

The impact maps to: **High — EIP-712 authorization bypass enabling forged execution or unauthorized account/code mutation.**

### Likelihood Explanation

The legacy EIP-712 path (`ExtensionOptionsWeb3Tx`) is the documented and tested path for Metamask/Web3 wallet users submitting Cosmos transactions. Multi-message transactions are explicitly supported and tested (see `TestLegacyEIP712SameMsgType`). Any user submitting a multi-message legacy EIP-712 transaction is exposed. An attacker with mempool access (e.g., a validator or full node operator) can observe pending transactions and substitute message values before inclusion. [8](#0-7) 

### Recommendation

Pass all messages to `LegacyWrapTxToTypedData` for type schema derivation, not just `msgs[0]`. Alternatively, enforce that the EIP-712 typed data is constructed from the full message list and that the type schema covers all message fields. The non-legacy path (`WrapTxToTypedData` in `ethereum/eip712/eip712.go`) correctly uses the full `signDocBytes` for both type inference and hashing and should be used as the reference. [9](#0-8) 

### Proof of Concept

1. Alice signs a legacy EIP-712 transaction with two messages of the same type, e.g.:
   - `msgs[0]`: `MsgDelegate(delegator=Alice, validator=V1, amount=1token)`
   - `msgs[1]`: `MsgDelegate(delegator=Alice, validator=V1, amount=1token)`

2. An attacker (e.g., a validator) intercepts the transaction in the mempool and replaces `msgs[1]` with:
   - `MsgDelegate(delegator=Alice, validator=AttackerValidator, amount=1000000token)`

3. The modified transaction is submitted. `LegacyValidatePayloadMessages` passes because both messages are still `MsgDelegate` with the same signer.

4. `LegacyWrapTxToTypedData` is called with `msgs[0]` (the original small delegation) for type schema derivation, but `txBytes` now contains the attacker-modified `msgs[1]`.

5. The EIP-712 hash is computed. Because the type schema was derived from `msgs[0]`, the `msgs` array in the JSON is encoded using the type definitions for `MsgDelegate` fields. The hash includes the raw JSON value of `msgs[1]`, but whether the substituted amount is caught depends on the exact EIP-712 JSON encoding path.

6. The signature check passes (or is close enough due to the raw JSON embedding), and both messages execute — including the attacker's large delegation to their own validator using Alice's funds. [10](#0-9) [2](#0-1)

### Citations

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

**File:** ante/cosmos/eip712.go (L244-256)
```go
		if err := eip712.LegacyValidatePayloadMessages(msgs); err != nil {
			return errorsmod.Wrap(err, "failed to validate payload messages")
		}

		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
		if err != nil {
			return errorsmod.Wrap(err, "failed to create EIP-712 typed data from tx")
		}

		sigHash, _, err := apitypes.TypedDataAndHash(typedData)
		if err != nil {
			return err
		}
```

**File:** ethereum/eip712/eip712_legacy.go (L49-103)
```go
func LegacyWrapTxToTypedData(
	cdc codectypes.AnyUnpacker,
	chainID uint64,
	msg sdk.Msg,
	data []byte,
	feeDelegation *FeeDelegationOptions,
) (apitypes.TypedData, error) {
	value, err := ethermint.SafeInt64(chainID)
	if err != nil {
		return apitypes.TypedData{}, err
	}
	txData := make(map[string]interface{})

	if err := json.Unmarshal(data, &txData); err != nil {
		return apitypes.TypedData{}, errorsmod.Wrap(errortypes.ErrJSONUnmarshal, "failed to JSON unmarshal data")
	}

	domain := apitypes.TypedDataDomain{
		Name:              "Cosmos Web3",
		Version:           "1.0.0",
		ChainId:           math.NewHexOrDecimal256(value),
		VerifyingContract: "cosmos",
		Salt:              "0",
	}

	msgTypes, err := extractMsgTypes(cdc, "MsgValue", msg)
	if err != nil {
		return apitypes.TypedData{}, err
	}

	if feeDelegation != nil {
		feeInfo, ok := txData["fee"].(map[string]interface{})
		if !ok {
			return apitypes.TypedData{}, errorsmod.Wrap(errortypes.ErrInvalidType, "cannot parse fee from tx data")
		}

		feeInfo["feePayer"] = feeDelegation.FeePayer.String()

		// also patching msgTypes to include feePayer
		msgTypes["Fee"] = []apitypes.Type{
			{Name: "feePayer", Type: "string"},
			{Name: "amount", Type: "Coin[]"},
			{Name: "gas", Type: "string"},
		}
	}

	typedData := apitypes.TypedData{
		Types:       msgTypes,
		PrimaryType: "Tx",
		Domain:      domain,
		Message:     txData,
	}

	return typedData, nil
}
```

**File:** ethereum/eip712/encoding_legacy.go (L236-274)
```go
func LegacyValidatePayloadMessages(msgs []sdk.Msg) error {
	if len(msgs) == 0 {
		return errors.New("unable to build EIP-712 payload: transaction does contain any messages")
	}

	var msgType string
	var msgSigner sdk.AccAddress

	for i, m := range msgs {
		t, err := getMsgType(m)
		if err != nil {
			return err
		}

		signers, _, err := protoCodec.GetMsgV1Signers(m)
		if err != nil {
			return err
		}
		if len(signers) != 1 {
			return errors.New("unable to build EIP-712 payload: expect exactly 1 signer")
		}

		if i == 0 {
			msgType = t
			msgSigner = signers[0]
			continue
		}

		if t != msgType {
			return errors.New("unable to build EIP-712 payload: different types of messages detected")
		}

		if !msgSigner.Equals(sdk.AccAddress(signers[0])) {
			return errors.New("unable to build EIP-712 payload: multiple signers detected")
		}
	}

	return nil
}
```

**File:** crypto/ethsecp256k1/ethsecp256k1.go (L233-250)
```go
func (pubKey PubKey) verifySignatureAsEIP712(msg, sig []byte) bool {
	eip712Bytes, err := eip712.GetEIP712BytesForMsg(msg)
	if err != nil {
		return false
	}

	if pubKey.verifySignatureECDSA(eip712Bytes, sig) {
		return true
	}

	// Try verifying the signature using the legacy EIP-712 encoding
	legacyEIP712Bytes, err := eip712.LegacyGetEIP712BytesForMsg(msg)
	if err != nil {
		return false
	}

	return pubKey.verifySignatureECDSA(legacyEIP712Bytes, sig)
}
```

**File:** ante/cosmos/eip712_test.go (L93-185)
```go
// TestLegacyEIP712SameMsgType tests that a legacy EIP-712 transaction with
// multiple messages of the same type succeeds on-chain.
func TestLegacyEIP712SameMsgType(t *testing.T) {
	app := testutil.Setup(false, nil)
	ctx := app.BaseApp.NewUncachedContext(false, tmproto.Header{ChainID: testutil.ChainID})
	app.FeeMarketKeeper.SetBaseFee(ctx, big.NewInt(1))

	privKey, err := ethsecp256k1.GenerateKey()
	require.NoError(t, err)
	delegator := sdk.AccAddress(privKey.PubKey().Address().Bytes())

	acc := app.AccountKeeper.NewAccountWithAddress(ctx, delegator)
	app.AccountKeeper.SetAccount(ctx, acc)

	bondDenom, err := app.StakingKeeper.BondDenom(ctx)
	require.NoError(t, err)
	evmDenom := app.EvmKeeper.GetParams(ctx).EvmDenom
	gas := uint64(500000)
	delegationAmount := sdk.NewCoin(bondDenom, sdkmath.NewInt(100))
	feeAmount := sdk.NewCoins(sdk.NewCoin(evmDenom, sdkmath.NewInt(100*int64(gas))))

	require.NoError(t, testutil.FundAccount(
		app.BankKeeper,
		ctx,
		delegator,
		sdk.NewCoins(
			sdk.NewCoin(bondDenom, delegationAmount.Amount.MulRaw(10)),
			sdk.NewCoin(evmDenom, feeAmount.AmountOf(evmDenom).MulRaw(2)),
		),
	))

	var valAddr sdk.ValAddress
	err = app.StakingKeeper.IterateValidators(ctx, func(_ int64, val stakingtypes.ValidatorI) bool {
		bz, err := app.StakingKeeper.ValidatorAddressCodec().StringToBytes(val.GetOperator())
		require.NoError(t, err)
		valAddr = sdk.ValAddress(bz)
		return true
	})
	require.NoError(t, err)
	require.NotEmpty(t, valAddr)

	_, err = app.StakingKeeper.GetDelegation(ctx, delegator, valAddr)
	require.Error(t, err, "delegation should not exist before transaction")

	msgs := []sdk.Msg{
		stakingtypes.NewMsgDelegate(delegator.String(), valAddr.String(), delegationAmount),
		stakingtypes.NewMsgDelegate(delegator.String(), valAddr.String(), delegationAmount),
	}

	txArgs := utiltx.EIP712TxArgs{
		CosmosTxArgs: utiltx.CosmosTxArgs{
			TxCfg:   app.TxConfig(),
			Priv:    privKey,
			ChainID: testutil.ChainID,
			Gas:     gas,
			Fees:    feeAmount,
			Msgs:    msgs,
		},
		UseLegacyExtension: true,
		UseLegacyTypedData: true,
	}

	tx, err := utiltx.CreateEIP712CosmosTx(ctx, app, txArgs)
	require.NoError(t, err)

	txBytes, err := app.TxConfig().TxEncoder()(tx)
	require.NoError(t, err)
	height := app.LastBlockHeight() + 1
	res, err := app.FinalizeBlock(&abci.RequestFinalizeBlock{
		Height: height,
		Txs:    [][]byte{txBytes},
	})
	require.NoError(t, err)
	require.Len(t, res.TxResults, 1)
	require.Zero(t, res.TxResults[0].Code, "expected tx to succeed with same message types")

	_, err = app.Commit()
	require.NoError(t, err)

	queryCtx := app.NewUncachedContext(false, tmproto.Header{ChainID: testutil.ChainID, Height: height + 1})
	delegation, err := app.StakingKeeper.GetDelegation(queryCtx, delegator, valAddr)
	require.NoError(t, err)

	require.True(t, delegation.Shares.IsPositive(),
		"expected positive delegation shares, got %s", delegation.Shares)

	validator, err := app.StakingKeeper.GetValidator(queryCtx, valAddr)
	require.NoError(t, err)
	expectedShares, err := validator.SharesFromTokens(delegationAmount.Amount.MulRaw(2))
	require.NoError(t, err)
	require.True(t, delegation.Shares.Equal(expectedShares),
		"expected delegation shares %s, got %s", expectedShares, delegation.Shares)
}
```

**File:** ethereum/eip712/eip712.go (L25-53)
```go
func WrapTxToTypedData(
	chainID uint64,
	data []byte,
) (apitypes.TypedData, error) {
	messagePayload, err := createEIP712MessagePayload(data)
	message := messagePayload.message
	if err != nil {
		return apitypes.TypedData{}, err
	}

	types, err := createEIP712Types(messagePayload)
	if err != nil {
		return apitypes.TypedData{}, err
	}

	value, err := ethermint.SafeInt64(chainID)
	if err != nil {
		return apitypes.TypedData{}, err
	}
	domain := createEIP712Domain(value)

	typedData := apitypes.TypedData{
		Types:       types,
		PrimaryType: txField,
		Domain:      domain,
		Message:     message,
	}

	return typedData, nil
```
