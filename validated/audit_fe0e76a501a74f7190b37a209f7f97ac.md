### Title
Chain-ID-less signature in `addr` precompile `associate()` allows cross-chain replay to force address association - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associate` method recovers an EVM/Sei address pair from a raw `(v, r, s)` signature over an arbitrary, caller-supplied `customMessage`, with no chain ID (or any other domain separator) bound into the signed payload, mirroring the root cause of the reported `PhiFactory::signatureClaim` bug: a signature that is valid on one chain is equally valid and replayable on every other chain that shares the same signature-recovery scheme.

### Finding Description
`PrecompileExecutor.associate` decodes `v`, `r`, `s`, and `customMessage` from calldata, computes `customMessageHash := crypto.Keccak256Hash([]byte(customMessage))`, and recovers `evmAddr`/`seiAddr`/`pubkey` purely from `(v, r, s, customMessageHash)` via `helpers.GetAddresses`: [1](#0-0) 

Nothing in the signed payload (`customMessage`) is required to include a chain identifier, and the precompile performs no chain-ID check before calling `associateAddresses`: [2](#0-1) 

This is architecturally identical to the report's root cause — a signature-recovery path that omits the chain ID from the data being signed/verified, enabling the same signature to be replayed on any chain implementing the identical scheme. Sei-chain itself explicitly recognizes and defends against this exact bug class elsewhere: the EIP-7702 `SetCodeAuthorization` pre-association logic in `AuthorityToPreAssociate` specifically checks `auth.ChainID` against the local chain and rejects mismatches, with an explicit comment warning that skipping this check "could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their direct-cast balance and orphaning staking/distribution state": [3](#0-2) 
This protection is validated by a dedicated regression test for the SetCode path: [4](#0-3) 

However, the `addr` precompile's `associate` entry point — reachable directly by any public EVM/RPC client calling the precompile at `0x0000000000000000000000000000000000001004` — has no equivalent chain-ID binding or check, unlike the SetCode authorization path that was hardened against exactly this scenario.

### Impact Explanation
Because the signed `customMessage` carries no chain-ID binding, a signature a user creates to associate their EVM/Sei address pair on one Sei chain (e.g., testnet, or an older/incompatible chain-id) can be captured (association transactions and their calldata are public) and replayed verbatim by anyone on any other Sei chain sharing the same address-derivation scheme to force the same association there. Per Sei's own documented threat model (the `AuthorityToPreAssociate` comment), forced/unwanted association triggers migration of the user's direct-cast bank balance and orphaning of staking/distribution state for that account — actions the user did not consent to performing on that particular chain at that particular time. This can disrupt account state (forced balance migration, orphaned staking rewards) without the victim's consent, which is the same category of harm the report describes as "unauthorized" action stemming from a chain-ID-agnostic signature.

### Likelihood Explanation
Likelihood is constrained by the fact that `associateAddresses` refuses to act if the target Sei address is already associated to an EVM address, limiting repeat/no-op replays. It is also constrained by whether Sei operates multiple chains (mainnet/testnet/devnet, or future chain-id changes/hard forks) that share the same underlying secp256k1 address-derivation scheme and accept the same `associate` precompile calldata format — which the codebase's own defensive comments on the SetCode path confirm is a real, anticipated multi-chain scenario. Any attacker or automated indexer monitoring one Sei chain's `associate` calls could harvest and replay them on another Sei chain with a single public transaction.

### Recommendation
Bind a chain identifier into the signed data verified by `associate`, analogous to how the EIP-7702 path checks `auth.ChainID` against `k.ChainID(ctx)`. Concretely, require `customMessage` to embed (or the precompile to independently verify) the current chain ID — e.g., reject the call unless the message hash includes `ctx.ChainID()`/`evmKeeper.ChainID(ctx)`, or require the message format to be a fixed, chain-ID-parameterized template that the precompile constructs itself and compares against, rather than trusting an arbitrary caller-supplied string.

### Proof of Concept
1. On Sei chain A (chain ID `X`), a user calls `associate` on the `addr` precompile with `(v, r, s, customMessage)`, where `(r, s)` is `secp256k1` sign of `keccak256(customMessage)`. This is observable on-chain as public calldata.
2. An attacker copies the exact same `(v, r, s, customMessage)` calldata.
3. The attacker submits an `associate` transaction with identical calldata to the `addr` precompile on Sei chain B (chain ID `Y != X`), where `evmAddr`/`seiAddr` is not yet associated.
4. `PrecompileExecutor.associate` at [5](#0-4)  recovers the identical `evmAddr`/`seiAddr`/`pubkey` (since neither the hash nor recovery depends on chain B's chain ID) and successfully calls `associateAddresses`, forcing the victim's association and associated balance-migration/staking-orphaning side effects on chain B without their authorization for that chain.

### Citations

**File:** precompiles/addr/addr.go (L160-203)
```go
func (p PrecompileExecutor) associate(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}

	// v, r and s are components of a signature over the customMessage sent.
	// We use the signature to construct the user's pubkey to obtain their addresses.
	v := args[0].(string)
	r := args[1].(string)
	s := args[2].(string)
	customMessage := args[3].(string)

	rBytes, err := decodeHexString(r)
	if err != nil {
		return nil, 0, err
	}
	sBytes, err := decodeHexString(s)
	if err != nil {
		return nil, 0, err
	}
	vBytes, err := decodeHexString(v)
	if err != nil {
		return nil, 0, err
	}

	vBig := new(big.Int).SetBytes(vBytes)
	rBig := new(big.Int).SetBytes(rBytes)
	sBig := new(big.Int).SetBytes(sBytes)

	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
}
```

**File:** precompiles/addr/addr.go (L239-255)
```go
func (p PrecompileExecutor) associateAddresses(ctx sdk.Context, method *abi.Method, evmAddr common.Address, seiAddr sdk.AccAddress, pubkey cryptotypes.PubKey) (ret []byte, remainingGas uint64, err error) {
	// Check that address is not already associated
	_, found := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if found {
		return nil, 0, fmt.Errorf("address %s is already associated with evm address %s", seiAddr, evmAddr)
	}

	// Associate Addresses:
	associationHelper := helpers.NewAssociationHelper(p.evmKeeper, p.bankKeeper, p.accountKeeper)
	err = associationHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false)
	if err != nil {
		return nil, 0, err
	}

	ret, err = method.Outputs.Pack(seiAddr.String(), evmAddr)
	return ret, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** utils/helpers/address.go (L149-188)
```go
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
func AuthorityToPreAssociate(ctx sdk.Context, k AuthorizationStateReader, auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, bool) {
	// Chain ID must be null or match the local chain.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.ChainID(ctx)) != 0 {
		return common.Address{}, nil, nil, false
	}
	// Nonce must not overflow (EIP-2681).
	if auth.Nonce+1 < auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	evmAddr, seiAddr, pubkey, err := RecoverAddressesFromAuthorization(auth)
	if err != nil {
		return common.Address{}, nil, nil, false
	}
	// Cross-check against go-ethereum's authoritative recovery so we only ever act on the
	// exact address SetCode would target during execution.
	if authAddr, aerr := auth.Authority(); aerr != nil || authAddr != evmAddr {
		return common.Address{}, nil, nil, false
	}
	// Authority must have no code, or only an existing delegation designator.
	if code := k.GetCode(ctx, evmAddr); len(code) != 0 {
		if _, ok := ethtypes.ParseDelegation(code); !ok {
			return common.Address{}, nil, nil, false
		}
	}
	// Authority account nonce must match the authorization nonce.
	if k.GetNonce(ctx, evmAddr) != auth.Nonce {
		return common.Address{}, nil, nil, false
	}
	// Already-associated authorities need no pre-association (and cannot be re-mapped).
	if _, associated := k.GetEVMAddress(ctx, seiAddr); associated {
		return common.Address{}, nil, nil, false
	}
	return evmAddr, seiAddr, pubkey, true
```

**File:** x/evm/ante/preprocess_test.go (L100-130)
```go
// TestPreprocessSkipsForeignChainSetCodeAuthority verifies that an EIP-7702 authorization
// signed for a DIFFERENT chain is not pre-associated. The authorization sig-hash uses the
// auth's own ChainID, so recovery succeeds, but the EVM would skip a wrong-chain auth at
// execution — so pre-associating it would let anyone replay a victim's public cross-chain
// authorization to force-associate them.
func TestPreprocessSkipsForeignChainSetCodeAuthority(t *testing.T) {
	k := &testkeeper.EVMTestApp.EvmKeeper
	ctx := testkeeper.EVMTestApp.GetContextForDeliverTx(nil)
	handler := ante.NewEVMPreprocessDecorator(k, k.AccountKeeper())
	chainID := k.ChainID(ctx)
	foreignChainID := new(big.Int).Add(chainID, big.NewInt(1)) // guaranteed != local chain

	sponsorKey, err := crypto.GenerateKey()
	require.NoError(t, err)
	victimKey, err := crypto.GenerateKey()
	require.NoError(t, err)
	victimEVM := crypto.PubkeyToAddress(victimKey.PublicKey)
	_, victimTrueSei, _, err := helpers.GetAddressesFromPubkeyBytes(crypto.FromECDSAPub(&victimKey.PublicKey))
	require.NoError(t, err)

	// Victim's authorization is signed for a foreign chain (e.g. Ethereum mainnet).
	auth, err := ethtypes.SignSetCode(victimKey, ethtypes.SetCodeAuthorization{
		ChainID: *uint256.MustFromBig(foreignChainID),
		Address: common.HexToAddress("0x000000000000000000000000000000000000c0de"),
		Nonce:   0,
	})
	require.NoError(t, err)
	// Sanity: recovery still resolves the victim (the sig-hash uses the auth's own ChainID).
	recoveredEVM, _, _, rerr := helpers.RecoverAddressesFromAuthorization(auth)
	require.NoError(t, rerr)
	require.Equal(t, victimEVM, recoveredEVM)
```
