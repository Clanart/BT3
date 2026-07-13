### Title
EIP-712 Domain Separator Uses Generic `VerifyingContract: "cosmos"` Enabling Cross-Chain Signature Replay - (File: `ethereum/eip712/domain.go`, `ethereum/eip712/eip712_legacy.go`)

### Summary

Both the modern and legacy EIP-712 encoding paths in Ethermint construct a domain separator with a hardcoded, generic `VerifyingContract: "cosmos"` string. This means any two Ethermint-based chains sharing the same numeric chain ID produce **identical EIP-712 domain separators**, allowing a valid user signature from one chain to be replayed verbatim on another chain with the same chain ID.

### Finding Description

The EIP-712 domain separator is constructed in two places:

**Modern path** — `ethereum/eip712/domain.go`, `createEIP712Domain`: [1](#0-0) 

**Legacy path** — `ethereum/eip712/eip712_legacy.go`, `LegacyWrapTxToTypedData`: [2](#0-1) 

Both produce a domain of the form:
```
{ Name: "Cosmos Web3", Version: "1.0.0", ChainId: <chainID>, VerifyingContract: "cosmos", Salt: "0" }
```

The `VerifyingContract` field is hardcoded to the literal string `"cosmos"` — a generic placeholder with no chain-instance-specific meaning. Per EIP-712, `verifyingContract` is the primary mechanism for distinguishing one application/deployment from another within the same chain ID space. Using a static string here means the domain separator is fully determined by the numeric chain ID alone.

These two functions are called from the signature verification paths:

- `WrapTxToTypedData` is called from `decodeAminoSignDoc` and `decodeProtobufSignDoc` in `ethereum/eip712/encoding.go`, which feed into `GetEIP712BytesForMsg`. [3](#0-2) 

- `LegacyWrapTxToTypedData` is called from `legacyDecodeAminoSignDoc` / `legacyDecodeProtobufSignDoc` in `ethereum/eip712/encoding_legacy.go`, and also directly from `VerifySignature` in `ante/cosmos/eip712.go`. [4](#0-3) 

The `PubKey.VerifySignature` method in `crypto/ethsecp256k1/ethsecp256k1.go` accepts a signature if it verifies under **either** the raw ECDSA path or the EIP-712 path (both modern and legacy encodings are tried): [5](#0-4) 

The chain ID used for domain construction is extracted from the sign doc bytes themselves (attacker-supplied in the tx body), not independently from the chain context: [6](#0-5) 

### Impact Explanation

Any two Ethermint-based chains that share the same numeric chain ID (e.g., a mainnet and a fork, a testnet and a staging environment, or two independent chains that happen to pick the same EVM chain ID) will produce **byte-for-byte identical EIP-712 domain separators**. A user's EIP-712-signed Cosmos transaction on Chain A is cryptographically indistinguishable from a transaction on Chain B. An attacker who observes a valid signed transaction on Chain A can submit it to Chain B without any modification. If the user holds funds on Chain B and the account number and sequence match (common after a fork or genesis copy), the transaction executes, transferring or delegating the user's funds without their consent on Chain B.

This maps directly to the allowed High impact: *"EIP-712 authorization, chain-id, or signer verification bypass enabling replay, forged execution, or unauthorized account/code mutation."*

### Likelihood Explanation

The precondition — two Ethermint chains sharing the same numeric chain ID — is realistic in several common scenarios:

1. **Testnets / staging chains** that deliberately mirror mainnet chain IDs.
2. **Hard forks** where the forked chain retains the original chain ID before updating it.
3. **Independent chains** that independently choose a popular EVM chain ID (e.g., 9001, 1337, 42).
4. **Chain ID collision** between an Ethermint chain and an EVM-compatible L2 that uses the same numeric ID.

Once the precondition is met, exploitation requires only that the attacker observe a broadcast EIP-712 Cosmos transaction on Chain A (publicly visible in the mempool or block explorer) and resubmit it to Chain B. No key material is needed.

### Recommendation

Replace the hardcoded `VerifyingContract: "cosmos"` with a chain-instance-specific identifier. Options include:

1. **Genesis hash**: Use the hex-encoded genesis block hash as `VerifyingContract`. This uniquely identifies each chain instance even when chain IDs collide.
2. **Chain-specific bech32 prefix + chain ID string**: Encode the full Cosmos chain ID string (e.g., `"evmos_9001-2"`) rather than just the numeric portion.
3. **Dedicated registry address**: Deploy or derive a deterministic address per chain and use it as `VerifyingContract`, mirroring the Futureswap fix.

The fix must be applied consistently in both `createEIP712Domain` (`ethereum/eip712/domain.go`) and the inline domain construction in `LegacyWrapTxToTypedData` (`ethereum/eip712/eip712_legacy.go`).

### Proof of Concept

1. Deploy two Ethermint chains, Chain A and Chain B, both with numeric chain ID `9001`.
2. On Chain A, user Alice signs and broadcasts an EIP-712 Cosmos `MsgSend` transferring 100 tokens to Bob. The domain separator is `{Name:"Cosmos Web3", Version:"1.0.0", ChainId:9001, VerifyingContract:"cosmos", Salt:"0"}`.
3. Alice also holds 100 tokens on Chain B, with the same account number and sequence (e.g., both chains share a genesis snapshot).
4. Attacker Eve observes Alice's signed transaction on Chain A (from the mempool or block explorer).
5. Eve submits the identical signed transaction bytes to Chain B's RPC endpoint.
6. Chain B's ante handler calls `VerifySignature` → `LegacyWrapTxToTypedData` → constructs the **identical** domain separator (same chain ID, same `VerifyingContract: "cosmos"`) → `apitypes.TypedDataAndHash` produces the same `sigHash` → `ethcrypto.VerifySignature` returns `true`. [7](#0-6) 
7. The transaction is accepted and executed on Chain B, transferring Alice's 100 tokens on Chain B to Bob without Alice's knowledge or consent.

### Citations

**File:** ethereum/eip712/domain.go (L24-33)
```go
func createEIP712Domain(chainID int64) apitypes.TypedDataDomain {
	domain := apitypes.TypedDataDomain{
		Name:              "Cosmos Web3",
		Version:           "1.0.0",
		ChainId:           math.NewHexOrDecimal256(chainID),
		VerifyingContract: "cosmos",
		Salt:              "0",
	}

	return domain
```

**File:** ethereum/eip712/eip712_legacy.go (L66-72)
```go
	domain := apitypes.TypedDataDomain{
		Name:              "Cosmos Web3",
		Version:           "1.0.0",
		ChainId:           math.NewHexOrDecimal256(value),
		VerifyingContract: "cosmos",
		Salt:              "0",
	}
```

**File:** ethereum/eip712/encoding.go (L118-131)
```go
	chainID, err := types.ParseChainID(aminoDoc.ChainID)
	if err != nil {
		return apitypes.TypedData{}, errors.New("invalid chain ID passed as argument")
	}

	typedData, err := WrapTxToTypedData(
		chainID.Uint64(),
		signDocBytes,
	)
	if err != nil {
		return apitypes.TypedData{}, fmt.Errorf("could not convert to EIP712 representation: %w", err)
	}

	return typedData, nil
```

**File:** ante/cosmos/eip712.go (L248-296)
```go
		typedData, err := eip712.LegacyWrapTxToTypedData(ethermintCodec, extOpt.TypedDataChainID, msgs[0], txBytes, feeDelegation)
		if err != nil {
			return errorsmod.Wrap(err, "failed to create EIP-712 typed data from tx")
		}

		sigHash, _, err := apitypes.TypedDataAndHash(typedData)
		if err != nil {
			return err
		}

		feePayerSig := extOpt.FeePayerSig
		if len(feePayerSig) != ethcrypto.SignatureLength {
			return errorsmod.Wrap(errortypes.ErrorInvalidSigner, "signature length doesn't match typical [R||S||V] signature 65 bytes")
		}

		// Remove the recovery offset if needed (ie. Metamask eip712 signature)
		if feePayerSig[ethcrypto.RecoveryIDOffset] == 27 || feePayerSig[ethcrypto.RecoveryIDOffset] == 28 {
			feePayerSig[ethcrypto.RecoveryIDOffset] -= 27
		}

		feePayerPubkey, err := ethcrypto.Ecrecover(sigHash, feePayerSig)
		if err != nil {
			return errorsmod.Wrap(err, "failed to recover delegated fee payer from sig")
		}

		ecPubKey, err := ethcrypto.UnmarshalPubkey(feePayerPubkey)
		if err != nil {
			return errorsmod.Wrap(err, "failed to unmarshal recovered fee payer pubkey")
		}

		pk := &ethsecp256k1.PubKey{
			Key: ethcrypto.CompressPubkey(ecPubKey),
		}

		if !pubKey.Equals(pk) {
			return errorsmod.Wrapf(errortypes.ErrInvalidPubKey, "feePayer pubkey %s is different from transaction pubkey %s", pubKey, pk)
		}

		recoveredFeePayerAcc := sdk.AccAddress(pk.Address().Bytes())

		if !recoveredFeePayerAcc.Equals(feePayer) {
			return errorsmod.Wrapf(errortypes.ErrorInvalidSigner, "failed to verify delegated fee payer %s signature", recoveredFeePayerAcc)
		}

		// VerifySignature of ethsecp256k1 accepts 64 byte signature [R||S]
		// WARNING! Under NO CIRCUMSTANCES try to use pubKey.VerifySignature there
		if !ethcrypto.VerifySignature(pubKey.Bytes(), sigHash, feePayerSig[:len(feePayerSig)-1]) {
			return errorsmod.Wrap(errortypes.ErrorInvalidSigner, "unable to verify signer signature of EIP712 typed data")
		}
```

**File:** crypto/ethsecp256k1/ethsecp256k1.go (L226-250)
```go
func (pubKey PubKey) VerifySignature(msg, sig []byte) bool {
	return pubKey.verifySignatureECDSA(msg, sig) || pubKey.verifySignatureAsEIP712(msg, sig)
}

// Verifies the signature as an EIP-712 signature by first converting the message payload
// to EIP-712 object bytes, then performing ECDSA verification on the hash. This is to support
// signing a Cosmos payload using EIP-712.
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
