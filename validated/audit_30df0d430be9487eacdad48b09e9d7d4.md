### Title
Native ERC20 conversion permanently locks user funds for non-`eth_secp256k1` (legacy Cosmos secp256k1 / multisig) accounts on IBC recv/refund - (File: x/erc20/keeper/ibc_callbacks.go)

### Summary
The IBC-ERC20 middleware's `OnRecvPacket` and `ConvertCoinToERC20FromPacket` functions convert a Cosmos `sdk.AccAddress` (decoded from the packet's `receiver`/`sender` string) directly into an EVM `common.Address` via `common.BytesToAddress(addr.Bytes())`, then credit/mint the corresponding native-ERC20 balance to that raw-byte-derived address. This silently assumes the 20-byte account identifier is also a valid, spendable Ethereum address for the same private key — true only for `eth_secp256k1` (Keccak-derived) accounts. Cosmos EVM chains explicitly still support classic `secp256k1` (RIPEMD160(SHA256(pubkey))-derived) accounts and multisig accounts, whose address bytes bear no cryptographic relationship to any EVM-recoverable address.

### Finding Description
`x/erc20/keeper/ibc_callbacks.go` decodes addresses using the local address codec and reuses the raw bytes as the EVM destination: [1](#0-0) [2](#0-1) 

and on refund/timeout: [3](#0-2) 

In both cases `common.BytesToAddress(recipient.Bytes())` / `common.BytesToAddress(sender)` is used to determine the EVM address that will hold the native-ERC20 balance minted via `ConvertCoinNativeERC20`. The chain, however, explicitly supports two different key derivation schemes producing different address bytes for the *same* private key/pubkey: [4](#0-3) 

A dedicated test in the repository (`TestAccountEquivalence`) demonstrates this exact divergence — deriving both a "legacy" Cosmos `secp256k1` address and an `eth_secp256k1` address from the same pubkey and proving they are unequal: [5](#0-4) 

Because `common.BytesToAddress` merely reinterprets bytes without any cryptographic check, the code silently trusts that whoever controls the bech32 AccAddress (ripemd160/sha256-derived, or a multisig hash, or any module-independent 20-byte identifier) also controls the private key needed to sign an Ethereum-style transaction recovering to that same 20 bytes via `keccak256(pubkey)[12:]`. This is exactly analogous to the reported `DepositsFallbackModule` bug: the code assumes an address on one representation/domain is "owned" by the same person in another domain, which does not hold for certain account types (legacy secp256k1 keys, Safe-style/Cosmos multisig accounts, or any account not created with the `eth_secp256k1` algorithm).

### Impact Explanation
When an ordinary IBC transfer (`OnRecvPacket`) targets a token pair marked `IsNativeERC20()` and the `receiver` field resolves to an account whose bech32 address was **not** derived via `eth_secp256k1`, the resulting native-ERC20 balance is minted to an EVM address that no private key the user holds can validly sign for (EVM `ecrecover` will only ever produce the Keccak-derived address for their key, never the RIPEMD/SHA256-derived one, and multisig accounts have no single controlling ECDSA key at all). Because this balance lives in EVM/ERC20 contract state (only spendable through a `msg.sender`-authenticated EVM call), the user has no path — via bank module or EVM call — to move or spend these tokens. The same applies to `ConvertCoinToERC20FromPacket`, invoked on `OnAcknowledgementPacket`/`OnTimeoutPacket` refunds, meaning a failed or timed-out outbound transfer sent by such an account also converts the refunded coin into a permanently stranded ERC20 balance. This is a Critical, permanent freezing/locking of user funds matching the in-scope impact "permanent freezing, locking ... of user funds ... or token-pair-backed balances."

### Likelihood Explanation
The trigger requires no privileged access: any unprivileged Cosmos user who (a) still uses a classic `secp256k1` key (explicitly supported per `SupportedAlgorithms` in `crypto/hd/algorithm.go`), (b) uses a legacy Cosmos multisig, or (c) receives funds at an address not derived via `eth_secp256k1`, and either receives an inbound IBC transfer of a native-ERC20-paired asset, or sends an outbound transfer of such an asset that later fails/times out, will hit this path. Given that Cosmos EVM chains are meant to interoperate with the broader IBC ecosystem (where most counterparty chains and their users are non-EVM and use classic Cosmos key types), this is a realistic and likely occurrence, not a contrived edge case.

### Recommendation
Do not assume raw-byte equivalence between a Cosmos `AccAddress` and an EVM `common.Address`. Before minting/crediting native-ERC20 balances via `ConvertCoinNativeERC20`, verify that the target account is registered/associated with a corresponding `eth_secp256k1` public key (e.g., check the account's `PubKey().Type()` via the account keeper), or otherwise avoid conversion for accounts whose key type cannot produce a valid EVM signature over the derived address. If no such association exists, fall back to leaving the funds as the native Cosmos coin (as is already done for the staking/bond denom and module accounts) rather than auto-converting to ERC20. Alternatively, expose an explicit, verifiable field/registration step (analogous to adding a dedicated `receiver` field in the referenced report) so that conversion only occurs when address-equivalence has been cryptographically proven, not merely assumed from raw bytes.

### Proof of Concept
1. Create a Cosmos account using the legacy `secp256k1` algorithm (still permitted by `SupportedAlgorithms` in `crypto/hd/algorithm.go`), yielding bech32 address `A` where `A ≠ keccak256(pubkey)[12:]` (as demonstrated by `TestAccountEquivalence` in `utils/utils_test.go:452-486`).
2. Register a token (e.g., an IBC voucher or bank coin) as a native-ERC20 token pair (`IsNativeERC20()==true`).
3. Have a counterparty chain send an ICS-20 transfer with `receiver = A` (bech32 string) for that denom.
4. `IBCMiddleware.OnRecvPacket` → `Keeper.OnRecvPacket` decodes `A` into `recipient` and calls `ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient)`, minting the ERC20 balance to `common.BytesToAddress(A.Bytes())`.
5. The legacy key holder cannot produce an EVM transaction whose `ecrecover` result equals `common.BytesToAddress(A.Bytes())` (their EVM-signature recovery always yields `keccak256(pubkey)[12:]`, a different address), so the minted ERC20 balance is permanently unreachable — demonstrating the fund-freezing impact.

### Citations

**File:** x/erc20/keeper/ibc_callbacks.go (L58-70)
```go
	// recipient (local chain address): accept hex or local bech32
	recipientBz, err := k.addrCodec.StringToBytes(data.Receiver)
	if err != nil {
		return channeltypes.NewErrorAcknowledgement(errorsmod.Wrap(err, "invalid recipient"))
	}
	recipient := sdk.AccAddress(recipientBz)

	receiverAcc := k.accountKeeper.GetAccount(ctx, recipient)

	// return acknowledgement without conversion if receiver is a module account
	if types.IsModuleAccount(receiverAcc) {
		return ack
	}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L119-139)
```go
	case found && pair.IsNativeERC20():
		// Token pair is disabled -> return
		if !pair.Enabled {
			return ack
		}

		pair, err := k.MintingEnabled(ctx, recipient, coin.Denom)
		if err != nil {
			ctx.EventManager().EmitEvent(
				sdk.NewEvent("erc20_callback_failure",
					sdk.NewAttribute(types.TypeMsgConvertCoin, "mint_failure"),
					sdk.NewAttribute(types.AttributeKeyCosmosCoin, coin.Denom),
					sdk.NewAttribute(types.AttributeKeyReceiver, recipient.String()),
				),
			)
			return channeltypes.NewErrorAcknowledgement(err)
		}

		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(recipient.Bytes()), recipient); err != nil {
			return channeltypes.NewErrorAcknowledgement(err)
		}
```

**File:** x/erc20/keeper/ibc_callbacks.go (L192-237)
```go
func (k Keeper) ConvertCoinToERC20FromPacket(ctx sdk.Context, data transfertypes.FungibleTokenPacketData) error {
	// Sender is local (source) chain address; accept local bech32 or 0x-hex
	senderBz, err := k.addrCodec.StringToBytes(data.Sender)
	if err != nil {
		return err
	}
	sender := sdk.AccAddress(senderBz)

	pairID := k.GetTokenPairID(ctx, data.Denom)
	pair, found := k.GetTokenPair(ctx, pairID)
	if !found {
		// no-op, token pair is not registered
		return nil
	}

	coin := ibc.GetSentCoin(data.Denom, data.Amount)

	switch {

	// Case 1. if pair is native coin -> no-op
	case pair.IsNativeCoin():
		// no-op, received coin is a  native coin
		return nil

	// Case 2. if pair is native ERC20 -> unescrow
	case pair.IsNativeERC20():
		// use a zero gas config to avoid extra costs for the relayers
		ctx = ctx.
			WithKVGasConfig(storetypes.GasConfig{}).
			WithTransientKVGasConfig(storetypes.GasConfig{})

		params := k.GetParams(ctx)
		if !params.EnableErc20 || !k.IsDenomRegistered(ctx, coin.Denom) {
			// no-op, ERC20s are disabled or the denom is not registered
			return nil
		}

		// assume that all module accounts on Cosmos EVM need to have their tokens in the
		// IBC representation as opposed to ERC20
		senderAcc := k.accountKeeper.GetAccount(ctx, sender)
		if types.IsModuleAccount(senderAcc) {
			return nil
		}

		// Convert from Coin to ERC20
		if err := k.ConvertCoinNativeERC20(ctx, pair, coin.Amount, common.BytesToAddress(sender), sender); err != nil {
```

**File:** crypto/hd/algorithm.go (L22-31)
```go
var (
	// SupportedAlgorithms defines the list of signing algorithms used on Cosmos EVM:
	//  - eth_secp256k1 (Ethereum)
	//  - secp256k1 (CometBFT)
	SupportedAlgorithms = keyring.SigningAlgoList{EthSecp256k1, hd.Secp256k1}
	// SupportedAlgorithmsLedger defines the list of signing algorithms used on Cosmos EVM for the Ledger device:
	//  - eth_secp256k1 (Ethereum)
	//  - secp256k1 (CometBFT)
	SupportedAlgorithmsLedger = keyring.SigningAlgoList{EthSecp256k1, hd.Secp256k1}
)
```

**File:** utils/utils_test.go (L452-486)
```go
	// calls:
	// sha := sha256.Sum256(pubKey.Key)
	// hasherRIPEMD160 := ripemd160.New()
	// hasherRIPEMD160.Write(sha[:])
	//
	// one way sha256 -> ripeMD160
	// this is the actual bech32 algorithm
	legacyAddress, err := legacyCosmosKey.GetAddress() //
	require.NoError(t, err)

	legacyPubKey, err := legacyCosmosKey.GetPubKey()
	require.NoError(t, err)

	// create an ethsecp key from the same exact pubkey bytes
	// this will mean that calling `Address()` will use the Keccack hash of the pubkey
	ethSecpPubkey := ethsecp256k1.PubKey{Key: legacyPubKey.Bytes()}

	// calls:
	// 	pubBytes := FromECDSAPub(&p)
	//	return common.BytesToAddress(Keccak256(pubBytes[1:])[12:])
	//
	// one way keccak hash
	// because the key implementation points to it to call the EVM methods
	ethSecpAddress := ethSecpPubkey.Address().Bytes()
	require.False(t, bytes.Equal(legacyAddress.Bytes(), ethSecpAddress))
	trueHexLegacy, err := utils.HexAddressFromBech32String(sdk.AccAddress(ethSecpAddress).String())
	require.NoError(t, err)

	// deriving a legacy bech32 from the legacy address
	legacyBech32Address := legacyAddress.String()

	// this just converts the ripeMD(sha(pubkey)) from bech32 formatting style to hex
	gotHexLegacy, err := utils.HexAddressFromBech32String(legacyBech32Address)
	require.NoError(t, err)
	require.NotEqual(t, trueHexLegacy.Hex(), gotHexLegacy.Hex())
```
