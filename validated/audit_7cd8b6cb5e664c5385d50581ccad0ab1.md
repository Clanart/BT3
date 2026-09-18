### Title
`associate`/`associatePublicKey` precompile accepts signatures with no chain/contract domain binding, enabling cross-context signature replay to force address association — ([File: precompiles/addr/addr.go])

### Summary
The `addr` precompile's `associate` method recovers an EVM/Sei address pair from an attacker-supplied `(v, r, s, customMessage)` tuple and immediately performs an address association and balance migration for whatever address the signature recovers to, with **no domain separation** in the signed payload: `customMessageHash := crypto.Keccak256Hash([]byte(customMessage))` is a bare keccak256 hash of an arbitrary caller-chosen string, containing no chain ID, no precompile/contract address, no "associate" purpose tag, and no personal-sign (`EIP-191`) prefix. [1](#0-0) 

This is the exact same bug class as CVE-2026-75156: the cryptographic signature is verified correctly (ECDSA recovery succeeds), but the code never validates the "audience"/"issuer" context the signed data was meant for, so a signature produced for one context (a different chain, a different contract, a different signing flow) is accepted as valid proof of intent here.

### Finding Description
`RecoverPubkey`/`GetAddresses` only check that the ECDSA signature is well-formed and recovers to *some* pubkey; they never verify that the signed hash was minted for this chain or this precompile. [2](#0-1) 

Compare this to the codebase's own EIP-7702 `SetCodeAuthorization` pre-association path, `AuthorityToPreAssociate`, whose doc comment explicitly identifies and defends against this exact bug class: because the authorization sig hash is computed only from the auth's own embedded `ChainID`, an authorization "signed for ANY chain" would otherwise be replayable to force-associate a victim and migrate their balance — so that function explicitly checks `auth.ChainID` against the local chain before acting. [3](#0-2) 

The `associate` precompile method has no equivalent check. `customMessage` is fully attacker/caller controlled plaintext, hashed with a plain (non-EIP-191, non-chain-bound) `Keccak256Hash`, and the resulting address pair is immediately fed into `associateAddresses` → `AssociateAddresses`, which:
- binds the pubkey to a Sei account,
- migrates the "direct-cast" address's `usei`/wei balances to the new Sei address,
- and can remove the old cast account. [4](#0-3) 

Because the signed digest carries no chain ID, contract address, or purpose string, any `(v, r, s)` signature a victim ever produced over data that happens to equal `keccak256(customMessage)` for some plaintext `customMessage` — e.g. a signature made for a different chain, a different dApp's login flow, or a future/legacy version of this same precompile — can be resubmitted by anyone as an `associate` call. The `Execute` dispatcher confirms this method is reachable by any EVM caller with no permission check other than "not readOnly". [5](#0-4) 

### Impact Explanation
An unprivileged transaction sender can force address association and an un-consented balance migration for a victim by replaying a signature/plaintext pair the victim produced for an unrelated context (no chain ID or precompile-address binding exists to prevent this). Per the sibling code's own documented threat model, this "migrat[es] their direct-cast balance and orphan[s] staking/distribution state" — i.e., permanent, victim-unintended state changes and potential permanent freezing/loss of staking and distribution entitlements tied to the old cast account, all without the victim submitting a Sei transaction. This satisfies the "permanent freezing" / "unauthorized transfer via precompile" impact bar.

### Likelihood Explanation
Exploitability depends on the attacker obtaining a `(customMessage plaintext, v, r, s)` pair produced by the victim's key where the digest has no domain separation — plausible wherever off-chain tooling signs a fixed/predictable message format for `associate` across environments (e.g., testnet vs. mainnet, or a legacy precompile version among the many near-identical `precompiles/addr/legacy/v*/addr.go` copies present in this codebase, all sharing the same unbound-hash scheme), or wherever a wallet signing flow reuses the same message text elsewhere. The precompile itself performs zero validation to rule this out, so likelihood is bounded only by external signature availability, not by any on-chain defense — unlike the `AuthorityToPreAssociate` path, which the codebase already hardened against precisely this class of replay.

### Recommendation
Bind the `associate`/`associatePublicKey` signed payload to a domain-separated hash analogous to `AuthorityToPreAssociate`'s chain-ID check: include the chain ID and the precompile/contract address (and ideally an explicit purpose tag) in the hash preimage before recovery, and reject any customMessage-derived digest that doesn't match the current chain/version, mirroring the checks already present at [6](#0-5) .

### Proof of Concept
1. Victim signs an arbitrary message `M` (v, r, s) in some other context where the raw digest happens to equal `keccak256(M)` — e.g. an older/legacy version of Sei's own `associate` flow, a different Sei network (testnet), or any tool that lets the user sign the exact bytes without EIP-191/chain binding.
2. Attacker calls the `addr` precompile's `associate(v, r, s, M)` with the leaked `(v, r, s, M)`, from any account, with no special permission: [7](#0-6) 
3. `GetAddresses` recovers the victim's `evmAddr`/`seiAddr`/`pubkey` purely from the signature validity, with no chain/contract check. [2](#0-1) 
4. `associateAddresses` → `AssociateAddresses` executes unconditionally, migrating the victim's direct-cast balance and binding their pubkey/account state without their consent for this chain/action. [4](#0-3)

### Citations

**File:** precompiles/addr/addr.go (L93-117)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, _ common.Address, _ common.Address, args []interface{}, value *big.Int, readOnly bool, _ *vm.EVM, suppliedGas uint64, hooks *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
		}
	}()
	switch method.Name {
	case GetSeiAddressMethod:
		return p.getSeiAddr(ctx, method, args, value)
	case GetEvmAddressMethod:
		return p.getEvmAddr(ctx, method, args, value)
	case Associate:
		if readOnly {
			return nil, 0, errors.New("cannot call associate precompile from staticcall")
		}
		return p.associate(ctx, method, args, value)
	case AssociatePubKey:
		if readOnly {
			return nil, 0, errors.New("cannot call associate pub key precompile from staticcall")
		}
		return p.associatePublicKey(ctx, method, args, value)
	}
	return
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

**File:** utils/helpers/address.go (L44-61)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
}

func GetAddressesFromPubkeyBytes(pubkey []byte) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	evmAddr, err := PubkeyToEVMAddress(pubkey)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	seiPubkey := PubkeyBytesToSeiPubKey(pubkey)
	seiAddr := sdk.AccAddress(seiPubkey.Address())
	return evmAddr, seiAddr, &seiPubkey, nil
}
```

**File:** utils/helpers/address.go (L149-160)
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
