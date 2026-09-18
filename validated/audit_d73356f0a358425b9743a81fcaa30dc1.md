### Title
Cross-Domain Signature Replay in `addr` Precompile's `associate` Function Enables Unauthorized Forced Address Association and Balance Sweep - (File: precompiles/addr/addr.go)

### Summary
The `associate` method of the `addr` precompile recovers an EVM/Sei address pair from an arbitrary `(v, r, s, customMessage)` tuple supplied by *any* caller and, if the derived Sei address is not yet associated, permissionlessly links the addresses and sweeps all funds sitting at the "cast" account into the newly associated Sei account. The digest that is signature-checked is a bare `keccak256(customMessage)` with no chain ID, contract address, or purpose-specific type hash mixed in, so any ECDSA signature the victim ever produced elsewhere (a different dApp, a different chain, a Sign-In-With-Ethereum flow, etc.) whose content happens to match `customMessage` can be replayed by an unrelated third party to force association on Sei, exactly the missing-domain-separation/replay class described in the reported issue.

### Finding Description
`associate` decodes `v/r/s` and a free-form `customMessage`, then recovers the signer via: [1](#0-0) 

`helpers.GetAddresses` hashes only the raw message bytes (`crypto.Keccak256Hash([]byte(customMessage))`), with no EIP-712-style domain separator, no chain ID, no precompile/contract address, and no nonce: [2](#0-1) 

Any address holding a valid `(v,r,s)` over any `customMessage` — regardless of who submits the transaction — can call `associate`, since there is no `msg.sender`/authorization check tying the caller to the recovered signer: [3](#0-2) 

Once accepted, `AssociateAddresses` unconditionally migrates all spendable coins and wei balances from the pre-existing "cast" account (`sdk.AccAddress(evmAddr[:])`) to the real Sei account and removes the cast account: [4](#0-3) 

Because the signed digest carries no domain-separation (no chain ID, no contract address, no explicit "for Sei address association" type hash, no nonce), a signature a user produced for an unrelated purpose — e.g., a personal_sign/EIP-191 message used for a Sign-In-With-Ethereum login on any dApp/chain, or a signature leaked/observed in an unrelated transaction — can be captured and replayed verbatim by any third party as the `associate` call's `(v, r, s, customMessage)` arguments. This mirrors the reported `castVoteBySig` issue: the missing nonce/domain binding lets a signature be reused outside its intended one-time context.

### Impact Explanation
This lets an unrelated, unprivileged transaction sender force a state-changing, irreversible action (address association) on behalf of a victim without their consent or awareness, at a time of the attacker's choosing, and trigger the automatic sweep of all balances (usei and wei) held at the victim's "cast" Sei account into the real Sei account via `MigrateBalance`. While the destination account is ultimately controlled by the same private key, the forced, attacker-timed sweep is an unauthorized transfer executed via a precompile using a signature that was never intended to authorize this action, and it can disrupt flows that rely on funds remaining at the unassociated cast address until the user chooses to associate (e.g., deposit flows expecting a specific account state), and is permanent/irreversible since there is no "un-associate" path.

### Likelihood Explanation
Likelihood is non-trivial: `personal_sign`/EIP-191 signatures are routinely produced by EOAs for logins and off-chain attestations across many dApps and chains, and are often publicly visible (e.g., posted for verification, embedded in API calls, or observable on other chains). Any caller can submit the `associate` transaction — no permission or fee beyond gas is required — making this reachable directly through the public EVM JSON-RPC surface described in the analog report's threat model.

### Recommendation
Bind the association digest to a Sei-specific, purpose-specific, and chain-specific domain: include the chain ID, the precompile address, a fixed "SEI_ASSOCIATE" type/purpose string, and consume a per-account nonce (or otherwise ensure the signed payload cannot have been produced for any other purpose/chain) before recovering and accepting the signature in `associate`/`GetAddresses`.

### Proof of Concept
1. Victim signs a message `M` with their EVM key for an unrelated purpose (e.g., a "Sign-In with Ethereum" login on a third-party dApp), producing `(v, r, s)` over `keccak256(M)` (or the raw hash if `M` is already a raw hash used elsewhere).
2. Attacker observes/obtains `(v, r, s, M)` from that unrelated context.
3. Attacker calls `addr.associate(v, r, s, M)` on Sei EVM.
4. `helpers.GetAddresses` recovers the victim's `evmAddr`/`seiAddr` pair from the signature; since the Sei address is unassociated, `associateAddresses` succeeds and `MigrateBalance` sweeps all funds from the victim's cast account into their Sei account — all without the victim submitting any Sei transaction or intending this action at this time.

### Citations

**File:** precompiles/addr/addr.go (L188-202)
```go

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
```

**File:** precompiles/addr/addr.go (L239-254)
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
```

**File:** utils/helpers/legacy/v600/address.go (L16-23)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
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
