Confirmed analog exists. The `associate` flow (both the `addr` precompile and `MsgAssociate`/`AssociateTx`) hashes an arbitrary, user-supplied `customMessage` with plain `keccak256` — no chain ID, no domain separator, no nonce, and no format requirement other than a 64-byte length cap — exactly the missing-domain-separation defect the external report flags for `Forwarder._verifySig`.

### Title
Chain-agnostic `associate` signature has no domain separation, enabling cross-context signature replay to force account association and balance migration - (File: `utils/helpers/associate.go`, `precompiles/addr/addr.go`, `app/ante/evm_checktx.go`)

### Summary
`MsgAssociate` / `AssociateTx` and the `addr` precompile's `associate(v,r,s,customMessage)` all recover a pubkey from an ECDSA signature over `keccak256(customMessage)`, where `customMessage` is an arbitrary, attacker-supplied byte string with no chain ID, contract address, purpose tag, or nonce baked into the signed digest.

### Finding Description
`HandleAssociateTx` in `app/ante/evm_checktx.go` computes `customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))` and recovers `(evmAddr, seiAddr, pubkey)` from it via `helpers.GetAddresses` [1](#0-0) . The same pattern is used in `PreprocessUnpacked` for the ante pipeline [2](#0-1)  and in the `addr` precompile's `associate` [3](#0-2) .

`ValidateBasic`/`Validate` on `MsgAssociate` and `AssociateTx` only enforce a 64-character length cap on `CustomMessage`; they never require it to encode a chain ID, contract address, or Sei-specific purpose string [4](#0-3) . Any valid secp256k1 signature over any byte string is accepted as long as the resulting `seiAddr` is not yet associated [5](#0-4) .

This is structurally identical to the reported bug: the signed digest carries no binding to the chain, contract, or protocol context, so any signature ever produced by the victim's key over a message the attacker can reproduce byte-for-byte (e.g., a "Sign in with Ethereum"/generic personal_sign flow used by another dApp, another Sei network such as testnet/devnet sharing the same key-derivation, or a future redeployment/fork) can be resubmitted on this chain to trigger the association side effects, without any interaction from the victim. This mirrors the exact class of defect that was explicitly fixed for the analogous EIP-7702 authority pre-association path, where the code deliberately checks `auth.ChainID` against the local chain ID and documents that doing so is "imperative" to prevent "replay[ing] a victim's public cross-chain authorization to force-associate them — migrating their direct-cast balance and orphaning staking/distribution state" [6](#0-5) . The plain `associate` path has no equivalent check.

### Impact Explanation
A successful forced association via a replayed signature triggers `AssociateAddresses`, which unconditionally migrates the victim's direct-cast address balance and wei balance into the newly associated true address, and removes the cast account: `MigrateBalance` calls `SendCoins`/`SendCoinsAndWei` from `castAddr` to `seiAddr` and then `RemoveAccount` for the cast address [7](#0-6) . Because this operation is irreversible (once associated, `HandleAssociateTx` rejects re-association: "account already has association set" [8](#0-7) ), a victim's cast-address funds can be permanently redirected to a pubkey-derived address the victim did not intend to bind at that time, and any staking/distribution state tied to the direct-cast identity can be orphaned — the same fund-loss and state-corruption class the maintainers themselves called out as high-severity risk for the related SetCode/EIP-7702 path.

### Likelihood Explanation
Exploitability depends on an attacker obtaining a signature the victim produced over a byte-identical `customMessage`/hash outside of this specific association flow (e.g., a signature from another chain, a different sei-chain network sharing key derivation, or a generic off-chain message-signing flow whose bytes happen to match). The `1 wei` balance-in-cast-account precondition for the ante-level `AssociateTx`/`MsgAssociate` path [9](#0-8)  and the "not already associated" precondition somewhat narrow the window, but the precompile's `associate` function has no minimum-balance gate at all [3](#0-2) , and any unassociated address with a matching signature is fully exploitable. Given the sponsor's own stated intent (documented for the sibling SetCode fix) to deploy consistently across environments/chains, the likelihood of colliding signed byte-strings is non-trivial once cross-network or cross-app signature reuse is considered.

### Recommendation
Bind the signed digest to Sei-specific, chain-specific context before hashing, mirroring the fix already applied for EIP-7702 authorizations:
1. Require `CustomMessage` to embed (and the ante/precompile code to verify) the local `chainID`, the requesting Sei/EVM address pair, and a freshness element (nonce or expiry), rather than accepting an arbitrary opaque string.
2. Alternatively, replace the ad-hoc `keccak256(customMessage)` scheme with a structured, versioned digest (e.g., an EIP-712-style domain separator including `block.chainid`/network identifier and a purpose tag "SEI_ASSOCIATE"), analogous to `AuthorityToPreAssociate`'s explicit chain-ID check on EIP-7702 authorizations [10](#0-9) .
3. Enforce this validation uniformly across all reachable associate entry points: `MsgAssociate` (`giga/deps/xevm/types/message_associate.go`, `x/evm/types/message_associate.go`), `AssociateTx` (`app/ante/evm_checktx.go`, `x/evm/ante/preprocess.go`), and every versioned `addr` precompile `associate` implementation (`precompiles/addr/addr.go` and its `legacy/v*` copies).

### Proof of Concept
1. Victim signs an arbitrary message `M` (e.g., via a generic personal_sign flow in a dApp unrelated to Sei, or on a different Sei network sharing the same key-derivation scheme) producing `(v, r, s)` over `keccak256(M)`.
2. Attacker observes this public signature and byte-identical `M` (e.g., from a public tx, log, or shared UX convention), and submits either:
   - a `MsgAssociate{Sender: <derived seiAddr>, CustomMessage: M}` Cosmos tx, or
   - an `addr` precompile call `associate(v, r, s, M)`.
3. `HandleAssociateTx`/`PreprocessUnpacked`/precompile `associate` recompute `keccak256(M)`, recover the same `(evmAddr, seiAddr, pubkey)` the victim would have produced, and — since no association exists yet and (for the ante path) the cast account holds ≥1 wei — accept the association [11](#0-10) .
4. `AssociateAddresses`/`MigrateBalance` moves all spendable coins and wei from the direct-cast address into the newly bound `seiAddr` and deletes the cast account [12](#0-11) , completing the forced, irreversible association and fund migration without the victim ever intending to associate on this chain/context.

### Citations

**File:** app/ante/evm_checktx.go (L182-203)
```go
func HandleAssociateTx(ctx sdk.Context, ek *evmkeeper.Keeper, atx *ethtx.AssociateTx, readOnly bool) (sdk.Context, error) {
	V, R, S := atx.GetRawSignatureValues()
	V = new(big.Int).Add(V, utils.Big27)
	// Hash custom message passed in
	customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))
	evmAddr, seiAddr, seiPubkey, err := helpers.GetAddresses(V, R, S, customMessageHash)
	if err != nil {
		return ctx, err
	}
	_, isAssociated := ek.GetEVMAddress(ctx, seiAddr)
	if isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	}
	if !IsAccountBalancePositive(ctx, ek, seiAddr, evmAddr) {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
	}
	if !readOnly {
		if err := AssociateAddress(ctx, ek, evmAddr, seiAddr, seiPubkey); err != nil {
			return ctx, err
		}
	}
	return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil
```

**File:** x/evm/ante/preprocess.go (L189-206)
```go
	if atx, ok := txData.(*ethtx.AssociateTx); ok {
		V, R, S := atx.GetRawSignatureValues()
		V = new(big.Int).Add(V, utils.Big27)
		// Hash custom message passed in
		customMessageHash := crypto.Keccak256Hash([]byte(atx.CustomMessage))
		evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(V, R, S, customMessageHash)
		if err != nil {
			return err
		}
		msgEVMTransaction.Derived = &derived.Derived{
			SenderEVMAddr: evmAddr,
			SenderSeiAddr: seiAddr,
			PubKey:        &secp256k1.PubKey{Key: pubkey.Bytes()},
			Version:       derived.Cancun,
			IsAssociate:   true,
		}
		return nil
	}
```

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

**File:** giga/deps/xevm/types/message_associate.go (L38-48)
```go
func (msg *MsgAssociate) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}
	if len(msg.CustomMessage) > MaxAssociateCustomMessageLength {
		return sdkerrors.Wrapf(sdkerrors.ErrTxTooLarge, "custom message can have at most 64 characters")
	}

	return nil
}
```

**File:** utils/helpers/address.go (L143-188)
```go
// AuthorityToPreAssociate returns the authority of an EIP-7702 authorization that should be
// associated with its true (pubkey-derived) Sei address before execution, or ok=false.
//
// It mirrors go-ethereum's StateTransition.validateAuthorization (chain id, nonce overflow,
// authority code, and account-nonce checks) so that pre-association happens only for
// authorizations the EVM will actually apply, and additionally skips authorities that are
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

**File:** utils/helpers/associate.go (L34-83)
```go
func (p AssociationHelper) AssociateAddresses(ctx sdk.Context, seiAddr sdk.AccAddress, evmAddr common.Address, pubkey cryptotypes.PubKey, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if !castAddr.Equals(seiAddr) && p.accountKeeper.GetAccount(ctx, seiAddr) == nil {
		castAcc := p.accountKeeper.GetAccount(ctx, castAddr)
		castBaseAcc, ok := castAcc.(*authtypes.BaseAccount)
		if ok && castBaseAcc.GetPubKey() == nil && p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
			p.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(seiAddr, pubkey, castBaseAcc.GetAccountNumber(), castBaseAcc.GetSequence()))
		}
	}
	p.evmKeeper.SetAddressMapping(ctx, seiAddr, evmAddr)
	acc := p.accountKeeper.GetAccount(ctx, seiAddr)
	if acc == nil {
		acc = p.accountKeeper.NewAccountWithAddress(ctx, seiAddr)
	}
	if acc.GetPubKey() == nil {
		if err := acc.SetPubKey(pubkey); err != nil {
			return err
		}
		p.accountKeeper.SetAccount(ctx, acc)
	}
	return p.MigrateBalance(ctx, evmAddr, seiAddr, migrateUseiOnly)
}

func (p AssociationHelper) MigrateBalance(ctx sdk.Context, evmAddr common.Address, seiAddr sdk.AccAddress, migrateUseiOnly bool) error {
	castAddr := sdk.AccAddress(evmAddr[:])
	if castAddr.Equals(seiAddr) {
		return nil
	}
	var castAddrBalances sdk.Coins
	if migrateUseiOnly {
		castAddrBalances = sdk.Coins{p.bankKeeper.GetBalance(ctx, castAddr, "usei")}
	} else {
		castAddrBalances = p.bankKeeper.SpendableCoins(ctx, castAddr)
	}
	if !castAddrBalances.IsZero() {
		if err := p.bankKeeper.SendCoins(ctx, castAddr, seiAddr, castAddrBalances); err != nil {
			return err
		}
	}
	castAddrWei := p.bankKeeper.GetWeiBalance(ctx, castAddr)
	if !castAddrWei.IsZero() {
		if err := p.bankKeeper.SendCoinsAndWei(ctx, castAddr, seiAddr, sdk.ZeroInt(), castAddrWei); err != nil {
			return err
		}
	}
	if p.bankKeeper.LockedCoins(ctx, castAddr).IsZero() {
		p.accountKeeper.RemoveAccount(ctx, authtypes.NewBaseAccountWithAddress(castAddr))
	}
	return nil
}
```
