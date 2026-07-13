### Title
Unauthorized Fee Deduction via `authInfo.Fee.Payer`/`extOpt.FeePayer` Binding Mismatch in Legacy EIP-712 Ante Handler — (`ante/cosmos/eip712.go::VerifySignature`, `evmd/ante/evm_handler.go`)

---

### Summary

The legacy EIP-712 ante handler chain deducts fees using `authInfo.Fee.Payer` (from the raw transaction's `AuthInfo`) **before** signature verification, but the EIP-712 typed data that the signer actually signs only commits to `extOpt.FeePayer` (from `ExtensionOptionsWeb3Tx`). These are two independent, unlinked fields. An unprivileged attacker can set `authInfo.Fee.Payer` to any victim address while setting `extOpt.FeePayer` to their own address, sign the typed data with their own key, and have fees deducted from the victim's account — all while passing signature verification.

---

### Finding Description

**Ante handler ordering** in `evmd/ante/evm_handler.go`:

```
authante.NewDeductFeeDecorator(...)          // line 50 — runs FIRST, deducts from authInfo.Fee.Payer
...
cosmos.NewLegacyEip712SigVerificationDecorator(...)  // line 56 — runs AFTER
``` [1](#0-0) 

**Fee deduction** (`authante.DeductFeeDecorator`) reads `feeTx.FeePayer()`, which returns `authInfo.Fee.Payer` if set, otherwise the first message signer. No signature or grant check is performed at this stage.

**Signature verification** in `VerifySignature` (`ante/cosmos/eip712.go`) reconstructs the typed data bytes using only `tx.GetFee()` and `tx.GetGas()` — `authInfo.Fee.Payer` is **never included**:

```go
txBytes := legacytx.StdSignBytes(
    signerData.ChainID, signerData.AccountNumber, signerData.Sequence,
    tx.GetTimeoutHeight(),
    legacytx.StdFee{Amount: tx.GetFee(), Gas: tx.GetGas()},  // no Payer, no Granter
    msgs, tx.GetMemo(),
)
``` [2](#0-1) 

The `feePayer` injected into the typed data comes exclusively from `extOpt.FeePayer`:

```go
feePayer, err := sdk.AccAddressFromBech32(extOpt.FeePayer)
feeDelegation := &eip712.FeeDelegationOptions{FeePayer: feePayer}
typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
``` [3](#0-2) 

`LegacyWrapTxToTypedData` injects `feeDelegation.FeePayer` into `fee.feePayer` of the typed data message:

```go
feeInfo["feePayer"] = feeDelegation.FeePayer.String()
``` [4](#0-3) 

The final signature check only verifies that the recovered address equals `extOpt.FeePayer`:

```go
if !recoveredFeePayerAcc.Equals(feePayer) { // feePayer == extOpt.FeePayer
    return errorsmod.Wrapf(...)
}
``` [5](#0-4) 

**The disconnect:** `authInfo.Fee.Payer` (used for actual fund deduction) and `extOpt.FeePayer` (committed to in the EIP-712 signature) are completely independent fields. Nothing in the ante handler chain checks that they are equal.

---

### Impact Explanation

An attacker can drain EVM-denom funds from any victim account that has a balance, without the victim's consent or signature. The attacker pays nothing; the victim's account is debited the full fee amount on every submitted transaction. This is unauthorized balance transfer of EVM-denom funds through Ethermint ante handler logic — matching the Critical/High impact criteria.

---

### Likelihood Explanation

The attack requires only:
- An attacker account on the chain (to be the message signer and produce a valid `extOpt.FeePayerSig`)
- A victim account with a non-zero balance
- The ability to submit a transaction (public mempool access)

No privileged roles, leaked keys, governance actions, or validator collusion are needed. The legacy EIP-712 path is explicitly wired in the production ante handler (`newLegacyCosmosAnteHandlerEip712`). [6](#0-5) 

---

### Recommendation

In `VerifySignature` (`ante/cosmos/eip712.go`), after recovering `extOpt.FeePayer`, assert that it equals `feeTx.FeePayer()` (i.e., `authInfo.Fee.Payer` if set, otherwise the first signer). Reject the transaction if they differ. Similarly, assert that `feeTx.FeeGranter()` is empty or matches a field committed to in the typed data. This closes the gap between what the signer authorizes and what the fee deduction logic actually charges.

---

### Proof of Concept

1. Attacker (`A`) has a valid account and private key. Victim (`V`) has a balance of N tokens.
2. Attacker constructs a Cosmos tx:
   - `authInfo.Fee.Payer` = `V` (victim's bech32 address)
   - `authInfo.Fee.Granter` = empty
   - `authInfo.Fee.Amount` = desired drain amount
   - `ExtensionOptionsWeb3Tx.FeePayer` = `A` (attacker's address)
   - `ExtensionOptionsWeb3Tx.TypedDataChainID` = correct chain ID
   - Messages: any valid Cosmos message signed by `A`
3. Attacker signs the EIP-712 typed data (which contains `fee.feePayer = A`) with their own key and sets `ExtensionOptionsWeb3Tx.FeePayerSig` to this signature.
4. Attacker broadcasts the transaction.
5. `authante.DeductFeeDecorator` runs: reads `feeTx.FeePayer()` = `V`, deducts fee from `V`'s account. No grant check (granter is empty).
6. `LegacyEip712SigVerificationDecorator` runs: recovers address from `FeePayerSig` = `A`, checks `A == extOpt.FeePayer` = `A` → **passes**.
7. Transaction commits. `V` has been charged without consent. Repeat to drain `V` completely.

### Citations

**File:** evmd/ante/evm_handler.go (L28-61)
```go
func newLegacyCosmosAnteHandlerEip712(ctx sdk.Context, options HandlerOptions, extra ...sdk.AnteDecorator) sdk.AnteHandler {
	evmParams := options.EvmKeeper.GetParams(ctx)
	feemarketParams := options.FeeMarketKeeper.GetParams(ctx)
	evmDenom := evmParams.EvmDenom
	chainID := options.EvmKeeper.ChainID()
	chainCfg := evmParams.GetChainConfig()
	ethCfg := chainCfg.EthereumConfig(chainID)
	var txFeeChecker authante.TxFeeChecker
	if options.DynamicFeeChecker {
		txFeeChecker = evm.NewDynamicFeeChecker(ethCfg, &evmParams, &feemarketParams)
	}
	decorators := make([]sdk.AnteDecorator, 0, 15+len(extra))
	decorators = append(decorators,
		cosmos.RejectMessagesDecorator{}, // reject MsgEthereumTxs
		// disable the Msg types that cannot be included on an authz.MsgExec msgs field
		cosmos.NewAuthzLimiterDecorator(options.DisabledAuthzMsgs),
		authante.NewSetUpContextDecorator(),
		authante.NewValidateBasicDecorator(),
		authante.NewTxTimeoutHeightDecorator(),
		cosmos.NewMinGasPriceDecorator(options.FeeMarketKeeper, evmDenom, &feemarketParams),
		authante.NewValidateMemoDecorator(options.AccountKeeper),
		authante.NewConsumeGasForTxSizeDecorator(options.AccountKeeper),
		authante.NewDeductFeeDecorator(options.AccountKeeper, options.BankKeeper, options.FeegrantKeeper, txFeeChecker),
		// SetPubKeyDecorator must be called before all signature verification decorators
		authante.NewSetPubKeyDecorator(options.AccountKeeper),
		authante.NewValidateSigCountDecorator(options.AccountKeeper),
		authante.NewSigGasConsumeDecorator(options.AccountKeeper, options.SigGasConsumer),
		// Note: signature verification uses EIP instead of the cosmos signature validator
		cosmos.NewLegacyEip712SigVerificationDecorator(options.AccountKeeper, options.SignModeHandler),
		authante.NewIncrementSequenceDecorator(options.AccountKeeper),
		ibcante.NewRedundantRelayDecorator(options.IBCKeeper),
	)
	decorators = append(decorators, extra...)
	return sdk.ChainAnteDecorators(decorators...)
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

**File:** ante/cosmos/eip712.go (L235-248)
```go
		feePayer, err := sdk.AccAddressFromBech32(extOpt.FeePayer)
		if err != nil {
			return errorsmod.Wrap(err, "failed to parse feePayer from ExtensionOptionsWeb3Tx")
		}

		feeDelegation := &eip712.FeeDelegationOptions{
			FeePayer: feePayer,
		}

		if err := eip712.LegacyValidatePayloadMessages(msgs); err != nil {
			return errorsmod.Wrap(err, "failed to validate payload messages")
		}

		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
```

**File:** ante/cosmos/eip712.go (L288-290)
```go
		if !recoveredFeePayerAcc.Equals(feePayer) {
			return errorsmod.Wrapf(errortypes.ErrorInvalidSigner, "failed to verify delegated fee payer %s signature", recoveredFeePayerAcc)
		}
```

**File:** ethereum/eip712/eip712_legacy.go (L85-92)
```go
		feeInfo["feePayer"] = feeDelegation.FeePayer.String()

		// also patching msgTypes to include feePayer
		msgTypes["Fee"] = []apitypes.Type{
			{Name: "feePayer", Type: "string"},
			{Name: "amount", Type: "Coin[]"},
			{Name: "gas", Type: "string"},
		}
```
