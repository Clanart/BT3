Based on my research, I found a concrete analog: sei-chain's `x/bank` coin `Metadata.Validate()` function performs no length limit on the `Name`, `Symbol`, or `Description` fields, unlike the tokenfactory module's own `subdenom`/`creator` fields, which *are* explicitly capped. This is directly reachable by any unprivileged, permissionless tokenfactory denom creator via `MsgSetDenomMetadata`.

### Title
Unbounded `Name`/`Symbol`/`Description` length in `x/bank` `Metadata.Validate()` allows permissionless tokenfactory denom admins to write near-tx-size state blobs — analog of rdiffweb's unbounded token-name DoS - (File: `sei-cosmos/x/bank/types/metadata.go`)

### Summary
`Metadata.Validate()`, used by both `x/bank`'s `SetDenomMetadata`/`MsgSetDenomMetadata` handler and by the permissionless `x/tokenfactory` module, only checks that `Name` and `Symbol` are non-blank and that `Base`/`Display` are valid denoms — it never bounds the byte length of `Name`, `Symbol`, or `Description`. This mirrors the reported rdiffweb bug class (CWE-770: allocation of resources without limits based on an unbounded "token name"-like field), while sibling fields in the same module (tokenfactory `subdenom`, `creator`) are explicitly capped for exactly this reason.

### Finding Description
`Metadata.Validate()` is: [1](#0-0) 

Notice it checks only `strings.TrimSpace(m.Name) == ""` and `strings.TrimSpace(m.Symbol) == ""`, with no upper bound on length for `Name`, `Symbol`, or `Description` (the latter isn't checked at all).

This validator is invoked as the "defense in depth" check in the tokenfactory `SetDenomMetadata` message handler, which any account that created a tokenfactory denom (permissionless via `MsgCreateDenom`) can call as that denom's admin: [2](#0-1) [3](#0-2) 

By contrast, the tokenfactory module explicitly caps `subdenom` (44 bytes) and `creator` (75 bytes) precisely to avoid unbounded string fields feeding into denom construction: [4](#0-3) 

There is no equivalent cap for `Metadata.Name`, `Metadata.Symbol`, or `Metadata.Description`, which get persisted verbatim into the bank keeper's KV store: [5](#0-4) 

Once stored, this metadata is iterated wholesale during genesis export/import (`IterateAllDenomMetaData`/`GetAllDenomMetaData`) and returned on-demand via the public bank gRPC/REST `DenomMetadata` query and via the EVM bank precompile's `denomMetadata` view function, which any public-RPC client can call without restriction: [6](#0-5) 

Because any account can permissionlessly create arbitrarily many tokenfactory denoms (`MsgCreateDenom` has no rate limit beyond gas/fees) and then call `SetDenomMetadata` on each with a `Name`/`Symbol`/`Description` sized up to the practical tx-byte limit, an attacker can accumulate large amounts of oversized metadata blobs in permanent chain state, each of which must subsequently be marshalled/iterated by every full node on every genesis export, state sync snapshot, and query response.

### Impact Explanation
This falls under CWE-770 (Allocation of Resources Without Limits), matching the reported bug class. Concrete consequences on sei-chain:
- Permanent, unbounded growth of on-chain bank denom metadata that must be stored and iterated by every node (`GetAllDenomMetaData`, genesis export), degrading node performance and increasing state size disproportionately versus the minimal per-tokenfactory-denom footprint the design otherwise intends (as evidenced by the explicit 44/75-byte caps elsewhere in the same module).
- Public RPC/gRPC endpoints (`bank.DenomMetadata` query, EVM `bank` precompile `denomMetadata`) can be made to return large payloads on demand for each maliciously created denom, amplifying load on any node answering these unauthenticated read requests.
- This does not rise to fund loss, but is a resource-exhaustion vector consistent with the "High" rdiffweb advisory's classification, though the practical severity on sei-chain is tempered because writes are still gas-metered and bounded by the consensus `MaxTxBytes`/`TxSizeCostPerByte`, which the rdiffweb bug's HTTP form field lacked entirely.

### Likelihood Explanation
High likelihood of a benign-looking but state-bloating pattern being triggered: any account can call `MsgCreateDenom` then `MsgSetDenomMetadata` with large `Name`/`Symbol`/`Description` values at minimal marginal cost (standard tx gas fees), repeatable indefinitely across many self-created denoms. No special privilege beyond being a tokenfactory denom admin (obtained for free by anyone) is required.

### Recommendation
Add explicit maximum-length checks for `Name`, `Symbol`, and `Description` in `Metadata.Validate()` (`sei-cosmos/x/bank/types/metadata.go`), analogous to the existing `MaxSubdenomLength`/`MaxCreatorLength` constants in `x/tokenfactory/types/denoms.go`. Reasonable bounds (e.g., tens to low hundreds of bytes) should be enforced consistently in both `x/bank`'s native `MsgSetDenomMetadata` path and the tokenfactory wrapper.

### Proof of Concept
1. Submit `MsgCreateDenom{Sender: attacker, Subdenom: "x"}` to create `factory/{attacker}/x` (permissionless).
2. Submit `MsgSetDenomMetadata{Sender: attacker, Metadata: {Base: "factory/{attacker}/x", Display: "usei", Name: <N bytes near max tx size>, Symbol: <M bytes>, Description: <large>, DenomUnits: [...]}}`.
3. `msg.Metadata.Validate()` in `x/tokenfactory/types/msgs.go` and the defense-in-depth check in `msg_server.go` both pass because no length bound exists on `Name`/`Symbol`/`Description`.
4. Repeat steps 1–2 across many distinct subdenoms/accounts to accumulate large amounts of oversized metadata in state, subsequently retrievable in bulk via `bank.DenomMetadata`/`AllDenomMetadata` gRPC queries or the EVM bank precompile's `denomMetadata` call, and re-processed on every genesis export/state sync.

### Citations

**File:** sei-cosmos/x/bank/types/metadata.go (L18-33)
```go
func (m Metadata) Validate() error {
	if strings.TrimSpace(m.Name) == "" {
		return errors.New("name field cannot be blank")
	}

	if strings.TrimSpace(m.Symbol) == "" {
		return errors.New("symbol field cannot be blank")
	}

	if err := sdk.ValidateDenom(m.Base); err != nil {
		return fmt.Errorf("invalid metadata base denom: %w", err)
	}

	if err := sdk.ValidateDenom(m.Display); err != nil {
		return fmt.Errorf("invalid metadata display denom: %w", err)
	}
```

**File:** x/tokenfactory/keeper/msg_server.go (L188-217)
```go
func (server msgServer) SetDenomMetadata(goCtx context.Context, msg *types.MsgSetDenomMetadata) (*types.MsgSetDenomMetadataResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	// Defense in depth validation of metadata
	err := msg.Metadata.Validate()
	if err != nil {
		return nil, err
	}

	authorityMetadata, err := server.GetAuthorityMetadata(ctx, msg.Metadata.Base)
	if err != nil {
		return nil, err
	}

	if msg.Sender != authorityMetadata.GetAdmin() {
		return nil, types.ErrUnauthorized
	}

	server.bankKeeper.SetDenomMetaData(ctx, msg.Metadata)

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.TypeMsgSetDenomMetadata,
			sdk.NewAttribute(types.AttributeDenom, msg.Metadata.Base),
			sdk.NewAttribute(types.AttributeDenomMetadata, msg.Metadata.String()),
		),
	})

	return &types.MsgSetDenomMetadataResponse{}, nil
}
```

**File:** x/tokenfactory/types/msgs.go (L211-228)
```go
func (m MsgSetDenomMetadata) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(m.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}

	err = m.Metadata.Validate()
	if err != nil {
		return err
	}

	_, _, err = DeconstructDenom(m.Metadata.Base)
	if err != nil {
		return err
	}

	return nil
}
```

**File:** x/tokenfactory/types/denoms.go (L12-38)
```go
const (
	ModuleDenomPrefix = "factory"
	// See the TokenFactory readme for a derivation of these.
	// TL;DR, MaxSubdenomLength + MaxHrpLength = 60 comes from SDK max denom length = 128
	// and the structure of tokenfactory denoms.
	MaxSubdenomLength = 44
	MaxHrpLength      = 16
	// MaxCreatorLength = 59 + MaxHrpLength
	MaxCreatorLength = 59 + MaxHrpLength
)

// GetTokenDenom constructs a denom string for tokens created by tokenfactory
// based on an input creator address and a subdenom
// The denom constructed is factory/{creator}/{subdenom}
func GetTokenDenom(creator, subdenom string) (string, error) {
	if len(subdenom) > MaxSubdenomLength {
		return "", ErrSubdenomTooLong
	}
	if len(creator) > MaxCreatorLength {
		return "", ErrCreatorTooLong
	}
	if strings.Contains(creator, "/") {
		return "", ErrInvalidCreator
	}
	denom := strings.Join([]string{ModuleDenomPrefix, creator, subdenom}, "/")
	return denom, sdk.ValidateDenom(denom)
}
```

**File:** sei-cosmos/x/bank/keeper/keeper.go (L377-384)
```go
// SetDenomMetaData sets the denominations metadata
func (k BaseKeeper) SetDenomMetaData(ctx sdk.Context, denomMetaData types.Metadata) {
	store := ctx.KVStore(k.storeKey)
	denomMetaDataStore := prefix.NewStore(store, types.DenomMetadataKey(denomMetaData.Base))

	m := k.cdc.MustMarshal(&denomMetaData)
	denomMetaDataStore.Set([]byte(denomMetaData.Base), m)
}
```

**File:** precompiles/bank/bank_test.go (L504-535)
```go
func TestDenomMetadataQuery(t *testing.T) {
	testApp := testkeeper.EVMTestApp
	ctx := testApp.NewContext(false, tmtypes.Header{}).WithBlockHeight(2)
	k := &testApp.EvmKeeper

	metadata := banktypes.Metadata{
		Description: "Test denom metadata",
		DenomUnits: []*banktypes.DenomUnit{
			{Denom: "udenommetaquery", Exponent: 0, Aliases: []string{"microdenommetaquery"}},
			{Denom: "denommetaquery", Exponent: 6, Aliases: []string{"DENOMMETAQUERY"}},
		},
		Base:    "udenommetaquery",
		Display: "denommetaquery",
		Name:    "Denom Meta Query",
		Symbol:  "DMQ",
	}
	k.BankKeeper().SetDenomMetaData(ctx, metadata)

	p, err := bank.NewPrecompile(testApp.GetPrecompileKeepers())
	require.Nil(t, err)
	statedb := state.NewDBImpl(ctx, k, true)
	evm := vm.EVM{StateDB: statedb}

	denomMetadata, err := p.ABI.MethodById(p.GetExecutor().(*bank.PrecompileExecutor).DenomMetadataID)
	require.Nil(t, err)
	args, err := denomMetadata.Inputs.Pack("udenommetaquery")
	require.Nil(t, err)
	res, _, err := p.RunAndCalculateGas(&evm, common.Address{}, common.Address{}, append(p.GetExecutor().(*bank.PrecompileExecutor).DenomMetadataID, args...), 100000, nil, nil, false, false)
	require.Nil(t, err)
	outputs, err := denomMetadata.Outputs.Unpack(res)
	require.Nil(t, err)
	require.Equal(t, 1, len(outputs))
```
