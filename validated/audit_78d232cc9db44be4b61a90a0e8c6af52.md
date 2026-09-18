Based on my research, I found a strong analog: the `EVMAddressDecorator` in `x/evm/ante/preprocess.go`, whose behavior is explicitly documented and tested by `TestEVMAddressDecoratorContinueDespiteErrors` in `x/evm/ante/preprocess_test.go`.

Note: I was unable to fetch the full body of `x/evm/ante/preprocess.go` before running out of tool iterations, so the exact downstream consequences of the decorator continuing past a verification failure (e.g., what specifically happens to the Sei↔EVM address mapping or subsequent balance operations) could not be independently confirmed from source. The finding below is based on the test file's explicit comments and assertions, which describe the code's intended (and verified via tests) behavior.

### Title
EVMAddressDecorator silently continues after pubkey/address-association verification fails, bypassing the Sei↔EVM address binding check - (File: x/evm/ante/preprocess.go)

### Summary
`ante.NewEVMAddressDecorator`'s `AnteHandle` is responsible for verifying/deriving the EVM↔Sei address association from the account's public key during the ante pipeline. The accompanying test `TestEVMAddressDecoratorContinueDespiteErrors` explicitly documents and asserts that when the account has no public key, a nil public key, or a public key that fails to parse, the decorator logs the error internally but returns `nil` (no error) from `AnteHandle`, allowing the transaction to proceed through the rest of the ante chain and be executed. [1](#0-0) [2](#0-1) 

### Finding Description
This mirrors the coreos-installer bug class: a verification step (there, GPG signature checking after gzip decompression; here, address/pubkey verification in an ante decorator) fails, the failure is logged, but the surrounding control flow treats it as success and proceeds anyway. In `coreos-installer`, this let an attacker-modified image pass through despite `gpg: BAD signature`. In sei-chain, a transaction whose sender account has a missing, nil, or malformed public key still passes `EVMAddressDecorator.AnteHandle` and continues to the next ante decorator and message execution, instead of being rejected at the point where address association could not be verified: [3](#0-2) [4](#0-3) [2](#0-1) 

The `EVMAddressDecorator` sits in the ante pipeline for the Sei↔EVM address-association bridge, which is explicitly called out as an in-scope reachable surface (address association between usei/wei state and the EVM StateDB relies on this mapping being correctly established/verified for the sending account).

### Impact Explanation
If address association/pubkey verification is bypassed rather than enforced, a transaction can be processed by downstream ante decorators and the EVM execution pipeline even though the sender's public key could not be validated. Depending on how later stages (e.g., signature verification decorators, or code that reads the derived EVM address for balance operations) rely on the decorator having already validated/set this association, this could allow the tx to proceed under an unverified or incorrect address linkage, which is the exact class of "verification silently bypassed" defect flagged by the external report.

### Likelihood Explanation
I could not fully confirm from the available source (limited to the test file, not the full decorator implementation) whether subsequent ante decorators (e.g., signature verification) still independently reject truly invalid transactions, which would limit the practical exploitability of this specific silent-continue behavior. The test's own comments ("Since the handler logs the error but does not stop processing, we expect no error returned") indicate this is an intentional/known design choice rather than an accidental leak, which lowers confidence that it is currently exploitable for fund loss versus being a best-effort/soft-fail address-association step backed by other independent signature checks elsewhere in the ante chain (e.g., `CheckSignatures` in `app/ante/cosmos_checktx.go`) that do properly enforce cryptographic signature validity and would still reject a forged transaction. [5](#0-4) 

### Recommendation
Have `EVMAddressDecorator.AnteHandle` return a non-nil error (or otherwise block progression) when it cannot verify/derive a valid Sei↔EVM address association due to a missing, nil, or unparsable public key, rather than logging and continuing, unless it can be proven that all downstream code paths independently enforce a strict pubkey/signature check that makes this decorator's failure inconsequential.

### Proof of Concept
Not applicable — I could not independently establish a concrete fund-loss or unauthorized-transfer path without access to the full `x/evm/ante/preprocess.go` source and the downstream consumers of the EVM address association, only the test file confirming the "continue despite errors" behavior.

### Citations

**File:** x/evm/ante/preprocess_test.go (L392-410)
```go
	// Prepare a SigVerifiableTx with no public key
	privKey := testkeeper.MockPrivateKey()
	sender, _ := testkeeper.PrivateKeyToAddresses(privKey)
	k.AccountKeeper().SetAccount(ctx, authtypes.NewBaseAccount(sender, &secp256k1.PubKey{}, 1, 1)) // deliberately no pubkey set
	msg := banktypes.NewMsgSend(sender, sender, sdk.NewCoins(sdk.NewCoin("usei", sdk.OneInt())))   // to self to simplify
	ctx, err = handler.AnteHandle(ctx, mockTx{msgs: []sdk.Msg{msg}, signers: []sdk.AccAddress{sender}}, false, func(ctx sdk.Context, _ sdk.Tx, _ bool) (sdk.Context, error) {
		return ctx, nil
	})
	// Since the handler logs the error but does not stop processing, we expect no error returned
	require.Nil(t, err, "Expected no error from AnteHandle despite missing public key")

	k.AccountKeeper().SetAccount(ctx, authtypes.NewBaseAccount(sender, nil, 1, 1))              // deliberately no pubkey set
	msg = banktypes.NewMsgSend(sender, sender, sdk.NewCoins(sdk.NewCoin("usei", sdk.OneInt()))) // to self to simplify
	ctx, err = handler.AnteHandle(ctx, mockTx{msgs: []sdk.Msg{msg}, signers: []sdk.AccAddress{sender}}, false, func(ctx sdk.Context, _ sdk.Tx, _ bool) (sdk.Context, error) {
		return ctx, nil
	})

	// Since the handler logs the error but does not stop processing, we expect no error returned
	require.Nil(t, err, "Expected no error from AnteHandle despite nil public key")
```

**File:** x/evm/ante/preprocess_test.go (L412-420)
```go
	// Prepare a SigVerifiableTx with a pubkey that fails to parse
	brokenPubKey := &secp256k1.PubKey{Key: []byte{1, 2, 3}} // deliberately too short to be valid
	k.AccountKeeper().SetAccount(ctx, authtypes.NewBaseAccount(sender, brokenPubKey, 1, 1))
	_, err = handler.AnteHandle(ctx, mockTx{msgs: []sdk.Msg{msg}, signers: []sdk.AccAddress{sender}}, false, func(ctx sdk.Context, _ sdk.Tx, _ bool) (sdk.Context, error) {
		return ctx, nil
	})

	// Since the handler logs the error but does not stop processing, we expect no error returned
	require.Nil(t, err, "Expected no error from AnteHandle despite inability to parse public key")
```

**File:** app/ante/cosmos_checktx.go (L503-515)
```go
		err = authsigning.VerifySignature(pubKey, signerData, sig.Data, txConfig.SignModeHandler(), tx)
		if err != nil {
			var errMsg string
			if authante.OnlyLegacyAminoSigners(sig.Data) {
				// If all signers are using SIGN_MODE_LEGACY_AMINO, we rely on VerifySignature to check account sequence number,
				// and therefore communicate sequence number as a potential cause of error.
				errMsg = fmt.Sprintf("signature verification failed; please verify account number (%d), sequence (%d) and chain-id (%s)", signerAcc.GetAccountNumber(), signerAcc.GetSequence(), chainID)
			} else {
				errMsg = fmt.Sprintf("signature verification failed; please verify account number (%d) and chain-id (%s)", signerAcc.GetAccountNumber(), chainID)
			}
			return nil, sdkerrors.Wrap(sdkerrors.ErrUnauthorized, errMsg)

		}
```
