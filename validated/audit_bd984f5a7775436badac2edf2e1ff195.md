### Title
`associatePublicKey` precompile lets anyone force an address association for a victim using only their public public key, with no signature-of-ownership check - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile exposes two ways to bind a Sei address to an EVM address: `associate` (requires an ECDSA signature `v,r,s` over a message, proving possession of the private key) and `associatePublicKey` (accepts a raw, uncompressed/compressed public key with **no signature at all**). [1](#0-0)  Both eventually call the same `associateAddresses`/`AssociateAddresses` helper, which sets the EVM↔Sei address mapping, installs the public key on the account if none is set, and migrates any balance sitting under the "direct-cast" address into the newly bound Sei address. [2](#0-1) [3](#0-2) 

### Finding Description
`associatePublicKey` decodes the caller-supplied bytes as a secp256k1 public key and derives `evmAddr`/`seiAddr`/`pubkey` directly from it via `helpers.GetAddressesFromPubkeyBytes`, without any accompanying signature proving the caller controls the corresponding private key: [4](#0-3) 

```go
func (p PrecompileExecutor) associatePublicKey(...) {
    pubKeyBytes, err := hex.DecodeString(pubKeyHex)
    ...
    pubKey, err := btcec.ParsePubKey(pubKeyBytes)
    ...
    evmAddr, seiAddr, pubkey, err := helpers.GetAddressesFromPubkeyBytes(uncompressedPubKey)
    ...
    return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
}
```

Compare this to `associate`, which requires a signature over `customMessage`, recovered via `crypto.Ecrecover`, and thus proves possession of the private key before any address mapping is created. [5](#0-4)  `associatePublicKey` skips this proof entirely — the only gate is "address not already associated": [6](#0-5) 

Public keys are not secret in Cosmos SDK chains: any account that has broadcast even one signed Cosmos transaction reveals its public key in the transaction's `auth_info`, and it remains queryable indefinitely via the account query. Consequently, **any unprivileged EVM caller can take a victim's already-public public key and call `associatePublicKey` on their behalf**, forcing:
- `SetAddressMapping` to bind the victim's Sei account to its EVM address, before the victim opts in. [7](#0-6) 
- Migration of any balance held at the "direct-cast" address (`sdk.AccAddress(evmAddr[:])`) — including `usei`/wei balances — into the victim's Sei address, and removal of the direct-cast account. [8](#0-7) 
- Setting of the account's on-chain public key if it had none.

This mirrors the CVE-2024-1643 root cause precisely: a state-changing "join/bind" action is gated only on knowledge of a public identifier (organization ID / public key) rather than on proof of the caller's actual authority over the resource being bound (organization membership / private-key ownership).

### Impact Explanation
While the destination of any migrated funds (the victim's own `seiAddr`) is nominally "correct," forcing this association without consent is exactly the kind of insufficiently-authorized state mutation the CVE targets, and it has concrete negative consequences reachable by any public RPC/EVM caller:
- It force-associates a victim's account before they intended to, without their signature — an availability/integrity violation of account-state that the design elsewhere (in `associate`, `x/evm/ante/preprocess.go`, and `AuthorityToPreAssociate`) treats as security-critical and deliberately signature-gates. [9](#0-8) [10](#0-9) 
- The `AuthorityToPreAssociate` comment for EIP-7702 explicitly documents that unsolicited/forced association is dangerous — "migrating their direct-cast balance and orphaning staking/distribution state" — showing the sei-chain team has treated forced association as a real threat class elsewhere but the `associatePublicKey` entrypoint reintroduces the same weakness at the account level, since it never validates that the caller actually owns the presented key. [11](#0-10) 
- Any locked/staking state that assumed the "direct-cast" identity was still independent could be orphaned once forced association migrates/removes that account, potentially interfering with unrelated protocol invariants (this exact orphaning concern is called out for the SetCode/authority path, and the same underlying `AssociateAddresses`/`MigrateBalance` code path is shared).

### Likelihood Explanation
High likelihood of exploitation reachability: `associatePublicKey` is a public, unauthenticated EVM precompile method (address `0x0000000000000000000000000000000000001004`) callable by any EVM transaction sender; the only "secret" required (the public key) is trivially obtainable for any account that has ever signed a Cosmos tx. [12](#0-11) 

### Recommendation
Require the same proof-of-ownership used by `associate` — e.g., require `associatePublicKey` to be signed by (or otherwise cryptographically tied to) the calling `msg.sender`/EOA whose account is being force-associated, or remove the public, unauthenticated variant and only allow the ante-handler/self-triggered association paths (`AssociateAddress` in `evm_checktx.go`, `EVMPreprocessDecorator.AnteHandle`) that already tie association to the actual transaction signer. [13](#0-12) 

### Proof of Concept
1. Observe any victim Sei account that has broadcast at least one signed Cosmos transaction (or query `/cosmos/auth/v1beta1/accounts/{address}`) to obtain their secp256k1 public key bytes.
2. From any unrelated EVM account, call the `addr` precompile's `associatePubKey(bytes pubkeyHex)` method with the victim's public key.
3. Observe that `SetAddressMapping` now binds the victim's `seiAddr` to their `evmAddr` without any signature from the victim, and any balance previously resting at the direct-cast address is migrated into `seiAddr` — all without the victim's consent or participation in the transaction. [14](#0-13) [3](#0-2)

### Citations

**File:** precompiles/addr/addr.go (L32-41)
```go
const (
	GetSeiAddressMethod = "getSeiAddr"
	GetEvmAddressMethod = "getEvmAddr"
	Associate           = "associate"
	AssociatePubKey     = "associatePubKey"
)

const (
	AddrAddress = "0x0000000000000000000000000000000000001004"
)
```

**File:** precompiles/addr/addr.go (L160-255)
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

func (p PrecompileExecutor) associatePublicKey(ctx sdk.Context, method *abi.Method, args []interface{}, value *big.Int) (ret []byte, remainingGas uint64, err error) {
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 1); err != nil {
		return nil, 0, err
	}

	// Takes a single argument, a compressed pubkey in hex format, excluding the '0x'
	pubKeyHex := args[0].(string)

	pubKeyBytes, err := hex.DecodeString(pubKeyHex)
	if err != nil {
		return nil, 0, err
	}

	// Parse the compressed public key
	pubKey, err := btcec.ParsePubKey(pubKeyBytes)
	if err != nil {
		return nil, 0, err
	}

	// Convert to uncompressed public key
	uncompressedPubKey := pubKey.SerializeUncompressed()

	evmAddr, seiAddr, pubkey, err := helpers.GetAddressesFromPubkeyBytes(uncompressedPubKey)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
}

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

**File:** x/evm/ante/preprocess.go (L57-114)
```go
//nolint:revive
func (p *EVMPreprocessDecorator) AnteHandle(ctx sdk.Context, tx sdk.Tx, simulate bool, next sdk.AnteHandler) (sdk.Context, error) {
	msg := evmtypes.MustGetEVMTransactionMessage(tx)
	if err := Preprocess(ctx, msg, p.evmKeeper.ChainID(ctx), p.evmKeeper.EthBlockTestConfig.Enabled); err != nil {
		return ctx, err
	}

	// use infinite gas meter for EVM transaction because EVM handles gas checking from within
	ctx = ctx.WithGasMeter(sdk.NewInfiniteGasMeterWithMultiplier(ctx))

	derived := msg.Derived
	seiAddr := derived.SenderSeiAddr
	evmAddr := derived.SenderEVMAddr
	ctx.EventManager().EmitEvent(sdk.NewEvent(evmtypes.EventTypeSigner,
		sdk.NewAttribute(evmtypes.AttributeKeyEvmAddress, evmAddr.Hex()),
		sdk.NewAttribute(evmtypes.AttributeKeySeiAddress, seiAddr.String())))
	pubkey := derived.PubKey
	isAssociateTx := derived.IsAssociate
	associateHelper := helpers.NewAssociationHelper(p.evmKeeper, p.evmKeeper.BankKeeper(), p.accountKeeper)
	_, isAssociated := p.evmKeeper.GetEVMAddress(ctx, seiAddr)
	if isAssociateTx && isAssociated {
		return ctx, sdkerrors.Wrap(sdkerrors.ErrInvalidRequest, "account already has association set")
	} else if isAssociateTx {
		// check if the account has enough balance (without charging)
		if !p.IsAccountBalancePositive(ctx, seiAddr, evmAddr) {
			assocErr := evmtypes.NewAssociationMissingErr(seiAddr.String())
			evmAnteMetrics.associationError.Add(ctx.Context(), 1, otelmetric.WithAttributes(attribute.String("scenario", "associate_tx_insufficient_funds"), attribute.String("type", assocErr.AddressType())))
			return ctx, sdkerrors.Wrap(sdkerrors.ErrInsufficientFunds, "account needs to have at least 1 wei to force association")
		}
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}

		return ctx.WithPriority(antedecorators.EVMAssociatePriority), nil // short-circuit without calling next
	} else if isAssociated {
		// noop; for readability
	} else {
		// not associatedTx and not already associated
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, pubkey, false); err != nil {
			return ctx, err
		}
		if p.evmKeeper.EthReplayConfig.Enabled {
			p.evmKeeper.PrepareReplayedAddr(ctx, evmAddr)
		}
	}

	// EIP-7702 authorization authorities are distinct accounts from the tx sender, so the
	// sender association above does not cover them. Associate each authority to its true
	// (pubkey-derived) Sei address before EVM execution installs delegation code for it.
	// Otherwise SetCode creates a mutable direct-cast EVM->Sei mapping that a later
	// associatePubKey call can remap, orphaning any staking/distribution state created
	// under the direct-cast identity (which can then halt the chain via the distribution
	// validator-removal hook).
	p.associateAuthorizationAuthorities(ctx, msg, associateHelper)

	return next(ctx, tx, simulate)
}

```

**File:** utils/helpers/address.go (L143-189)
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
}
```

**File:** app/ante/evm_checktx.go (L240-249)
```go
func AssociateAddress(ctx sdk.Context, ek *evmkeeper.Keeper, evmAddr common.Address, seiAddr sdk.AccAddress, seiPubkey cryptotypes.PubKey) error {
	_, isAssociated := ek.GetEVMAddress(ctx, seiAddr)
	if !isAssociated {
		associateHelper := helpers.NewAssociationHelper(ek, ek.BankKeeper(), ek.AccountKeeper())
		if err := associateHelper.AssociateAddresses(ctx, seiAddr, evmAddr, seiPubkey, false); err != nil {
			return err
		}
	}
	return nil
}
```
