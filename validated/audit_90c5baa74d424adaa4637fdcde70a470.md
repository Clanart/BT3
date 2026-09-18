Confirmed: `AssociateAddresses` migrates any spendable balance and wei balance from the direct-cast address (`sdk.AccAddress(evmAddr[:])`) to the pubkey-derived `seiAddr` whenever association happens, and this migration is triggered unconditionally by the `associate()` precompile path shown above.

### Title
Authentication-bypass-by-signature-reuse in the `addr` precompile's `associate()` allows forced EVM↔Sei address association without domain separation - (File: precompiles/addr/addr.go)

### Summary
The `addr` precompile's `associate(v, r, s, customMessage)` method (reachable by any EVM caller/contract) recovers a pubkey from an attacker-supplied `(v, r, s)` signature over `keccak256(customMessage)`, where `customMessage` is a completely free-form string chosen by the caller, with no fixed prefix/domain-separator/chain-id/nonce enforced on-chain. This mirrors the samlify class of bug (CWE-347): a signature produced by a victim for one context/message can be reused by anyone else in an entirely different context (here, the "consent to associate EVM/Sei addresses" flow) to trigger a privileged state change the signer never intended, because the code checks only "is this a valid ECDSA signature over *some* hash," not "was this signature specifically created to authorize this action against this domain."

### Finding Description
`PrecompileExecutor.associate` in `precompiles/addr/addr.go` decodes `v/r/s` and an arbitrary `customMessage`, computes `customMessageHash := crypto.Keccak256Hash([]byte(customMessage))`, and calls `helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)` which does a raw `crypto.Ecrecover` with no context binding: [1](#0-0) [2](#0-1) 

The only client-side convention that ties a signature to "intent to associate" is the fixed string `"Please sign this message to link your EVM and Sei addresses..."` used by the SDK/tests, but this text is never validated on-chain — `associate()` accepts *any* `customMessage`: [3](#0-2) 

Because there is no domain separator (no chain ID, no contract address, no nonce, no fixed EIP-191/712 typed-data binding enforced by the precompile itself), any ECDSA signature the victim has ever produced with their key over a message string an attacker can also reconstruct is directly usable here. This is architecturally the same defect class as XML Signature Wrapping in samlify: the cryptographic signature check succeeds, but the signed *content* was never bound to "authorize this specific privileged action," so a signature minted for context A gets accepted as authorization for context B.

The impact is not merely a metadata write: `associateAddresses` → `AssociationHelper.AssociateAddresses` → `MigrateBalance` unconditionally sweeps all spendable coins and wei balance from the direct-cast address `sdk.AccAddress(evmAddr[:])` to the derived `seiAddr`, and removes the cast account if unlocked: [4](#0-3) 

The codebase's own comments acknowledge this exact bug class is dangerous elsewhere (EIP-7702 authorization replay), explicitly calling out that an unbound signature "could be replayed... to force-associate them — migrating their direct-cast balance and orphaning staking/distribution state," and mitigates it there with chain-ID/nonce/authority cross-checks: [5](#0-4) 

No equivalent binding exists for the `associate()` precompile path itself — `customMessage` is hashed and used as-is with no required prefix check, no chain ID, and no nonce.

### Impact Explanation
Any third party who obtains a signature the victim produced for an unrelated purpose (e.g., a `personal_sign`/arbitrary message signature collected by a malicious dApp, or a signature captured from any other application, chain, or protocol that happens to use compatible free-text signing) can submit that `(v,r,s,message)` tuple to `associate()` and force a permanent, irreversible EVM↔Sei address linkage for the victim before the victim intended to opt in. Since `AssociateAddresses`/`MigrateBalance` is triggered as part of this same call, this also forcibly sweeps balances sitting at the direct-cast address into the newly-associated account and can delete the cast account, i.e., "unauthorized transfer... fee or refund abuse" style fund movement performed against the victim's wishes and without a genuine consent signature for that specific action, satisfying the "concrete fund loss" bar for this scan.

### Likelihood Explanation
Medium-to-High: exploitation requires the attacker to possess a signature the victim made over a message that, when reconstructed as `customMessage`, hashes identically. This is realistic in practice: browser wallets routinely produce arbitrary `personal_sign` signatures for various dApps/login flows using attacker-influenced or predictable message templates, and captured signatures are frequently public (e.g., posted on-chain elsewhere, leaked via other dApps, or intentionally solicited by a malicious frontend under a pretext unrelated to Sei). No special privileges beyond "submit an EVM transaction calling the `addr` precompile" are needed by the attacker.

### Recommendation
Enforce a fixed, non-attacker-controllable domain-separated message format on-chain in `associate()` instead of trusting the caller-supplied `customMessage` verbatim: require the hashed content to equal a canonical, chain-bound template (e.g., include chain ID and/or a monotonically increasing per-account nonce, similar to how `AuthorityToPreAssociate` cross-checks chain ID/nonce for EIP-7702). Reject any `customMessage` that doesn't match the expected canonical association-intent string plus chain-id binding, mirroring the mitigation already applied to EIP-7702 authorization replay.

### Proof of Concept
1. Attacker deploys/uses any dApp that requests the victim sign an arbitrary text message via `personal_sign` (unrelated cover story), where the exact byte content of the message is known/reconstructable by the attacker.
2. Attacker extracts `(v, r, s)` from that signature and passes `customMessage` = the exact signed text into `addr.associate(v, r, s, customMessage)` on Sei EVM.
3. `helpers.GetAddresses` recovers the victim's pubkey/EVM address/Sei address purely from signature validity over `keccak256(customMessage)`.
4. `associateAddresses` succeeds (since the victim's Sei address was not yet associated), permanently linking the addresses and invoking `MigrateBalance`, which sweeps the victim's spendable/wei balance from the direct-cast Sei address to the derived Sei address and can delete the cast account — all without the victim ever intending to trigger Sei address association.

### Citations

**File:** precompiles/addr/addr.go (L169-202)
```go
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
```

**File:** utils/helpers/address.go (L44-51)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
}
```

**File:** utils/helpers/address.go (L143-156)
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
```

**File:** integration_test/precompile_tests/precompiles/addr.spec.ts (L26-38)
```typescript
/** The canonical association message users sign (mirrors the legacy hardhat suite). */
const ASSOCIATE_MESSAGE =
    'Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n';

/** EIP-191 envelope the precompile hashes: "\x19Ethereum Signed Message:\n<len><message>". */
const eip191Envelope = (message: string): string =>
    `\x19Ethereum Signed Message:\n${Buffer.from(message, 'utf8').length}${message}`;

/** v/r/s in the precompile's expected shape: v is the 0/1 recovery id as hex. */
const signatureParts = async (wallet: EvmAccount, message: string) => {
    const sig = ethers.Signature.from(await wallet.wallet.signMessage(message));
    return { v: `0x${sig.v - 27}`, r: sig.r, s: sig.s };
};
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
