### Title
Non-CHIP-0002 "raw" message signing (`safe_mode=False`) lets an RPC/dApp caller obtain a wallet signature reusable as an unauthenticated `AGG_SIG_UNSAFE` spend authorization - (File: `chia/wallet/util/signing.py`)

### Summary
The FreeSWITCH bug (ALPINE-CVE-2021-41158) is a class of "the responder computes a credential-derived response to an attacker-supplied challenge/context without verifying that the challenge actually belongs to a legitimate protocol transaction for that credential," letting the attacker harvest a usable secret-bound value. The reachable analog in this codebase is the wallet's message-signing RPC path: when `safe_mode=False`, `sign_message()` signs the attacker-supplied bytes directly with the coin-owning key, with no domain separation and no binding to a coin, puzzle hash, or the CHIP-0002 `"Chia Signed Message"` prefix. That raw `(pubkey, msg)` BLS signature is exactly the shape required by the `AGG_SIG_UNSAFE` condition, which itself is not bound to any particular coin identity.

### Finding Description
`signing_mode_enum()` chooses `BLS_MESSAGE_AUGMENTATION_UTF8_INPUT`/`BLS_MESSAGE_AUGMENTATION_HEX_INPUT` whenever the caller sets `safe_mode=False`: [1](#0-0) 

`sign_message()` then signs the *raw* bytes with no CHIP-0002 prefix wrapping in that mode, unlike the default path which wraps the message in `(CHIP_0002_SIGN_MESSAGE_PREFIX, message)` before hashing: [2](#0-1) 

This is invoked from the local wallet RPC endpoints `sign_message_by_address` and `sign_message_by_id`, which resolve the *actual spending* synthetic secret key for an address/DID/NFT and sign whatever message string the caller supplies, with `safe_mode` defaulting to `True` but explicitly overridable by the caller: [3](#0-2) [4](#0-3) 

The resulting `(pubkey, msg, signature)` triple is precisely what `AGG_SIG_UNSAFE` consumes on-chain: it is not bound to a coin id, parent, puzzle hash, or amount, and mempool/consensus code only rejects it if the message *ends with* one of the domain-separated `AGG_SIG_*_ADDITIONAL_DATA` suffixes — it otherwise accepts an arbitrary attacker-chosen message: [5](#0-4) 

So a caller who can get this RPC invoked with `safe_mode=False` (e.g., a local dApp/websocket client requesting the user "sign this message" for an unrelated purpose) can choose `msg` to be exactly the string they need for an `AGG_SIG_UNSAFE` condition in a spend bundle that spends any coin controlled by that key, then push that spend bundle to the mempool — turning a "sign this text" prompt into full transaction authorization, the same way the FreeSWITCH bug turns an attacker-controlled challenge/realm into a usable credential-derived value.

### Impact Explanation
`AGG_SIG_UNSAFE` signatures are not scoped to any coin, so a signature harvested via the "message signing" endpoint with `safe_mode=False` can be repurposed to authorize spending coins owned by the signing key, i.e. unauthorized/forged coin movement. This is a direct blind-signing risk: the wallet UI/RPC caller believes they are only signing an inert message, but the resulting signature is cryptographically indistinguishable from a spend authorization.

### Likelihood Explanation
Requires only a local RPC/wallet client interaction (in-scope) requesting `sign_message_by_address`/`sign_message_by_id` with `safe_mode=False` and an attacker-chosen message string, plus the ability to submit a matching `AGG_SIG_UNSAFE` spend bundle to the mempool — both reachable by an unprivileged local caller/dApp integration without any node compromise.

### Recommendation
Disallow (or strongly discourage/warn on) `safe_mode=False` raw signing for keys that are also used for coin spending, or enforce that raw/unsafe-mode messages cannot collide with valid `AGG_SIG_UNSAFE` payloads (e.g., always require a domain-separating prefix distinct from anything CLVM can produce, and reject raw signing entirely for spend-capable keys through the RPC).

### Proof of Concept
1. Attacker-controlled application asks the local wallet RPC to sign an arbitrary byte string via `sign_message_by_address(message=<crafted_bytes>, is_hex=True, safe_mode=False)`.
2. `signing_mode_enum` selects `BLS_MESSAGE_AUGMENTATION_HEX_INPUT`; `sign_message()` signs `<crafted_bytes>` directly with the address's synthetic secret key, with no CHIP-0002 prefix (`chia/wallet/util/signing.py:72-85`).
3. Attacker builds a `CoinSpend` for a coin owned by that key with solution containing `(AGG_SIG_UNSAFE pubkey <crafted_bytes>)`.
4. Attacker submits a `SpendBundle` using the harvested signature; `pkm_pairs_for_conditions_dict`/mempool validation accept it since `<crafted_bytes>` doesn't end with any `AGG_SIG_*_ADDITIONAL_DATA` suffix (`chia/consensus/condition_tools.py:140-145`), completing an unauthorized spend.

### Citations

**File:** chia/wallet/wallet_request_types.py (L422-430)
```python
def signing_mode_enum(request: SignMessageByAddress | SignMessageByID) -> SigningMode:
    if request.is_hex and request.safe_mode:
        return SigningMode.CHIP_0002_HEX_INPUT
    elif not request.is_hex and not request.safe_mode:
        return SigningMode.BLS_MESSAGE_AUGMENTATION_UTF8_INPUT
    elif request.is_hex and not request.safe_mode:
        return SigningMode.BLS_MESSAGE_AUGMENTATION_HEX_INPUT

    return SigningMode.CHIP_0002
```

**File:** chia/wallet/wallet_request_types.py (L433-461)
```python
@streamable
@dataclass(kw_only=True, frozen=True)
class SignMessageByAddress(Streamable):
    address: str
    message: str
    is_hex: bool = False
    safe_mode: bool = True

    @property
    def signing_mode_enum(self) -> SigningMode:
        return signing_mode_enum(self)


@streamable
@dataclass(kw_only=True, frozen=True)
class SignMessageByAddressResponse(Streamable):
    pubkey: G1Element
    signature: G2Element
    signing_mode: str


@streamable
@dataclass(kw_only=True, frozen=True)
class SignMessageByID(Streamable):
    id: str
    message: str
    is_hex: bool = False
    safe_mode: bool = True

```

**File:** chia/wallet/util/signing.py (L72-85)
```python
def sign_message(secret_key: PrivateKey, message: str, mode: SigningMode) -> SignMessageResponse:
    public_key = secret_key.get_g1()
    if mode == SigningMode.CHIP_0002_HEX_INPUT:
        hex_message: bytes = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, bytes.fromhex(message))).get_tree_hash()
    elif mode == SigningMode.BLS_MESSAGE_AUGMENTATION_UTF8_INPUT:
        hex_message = bytes(message, "utf-8")
    elif mode == SigningMode.BLS_MESSAGE_AUGMENTATION_HEX_INPUT:
        hex_message = bytes.fromhex(message)
    else:
        hex_message = Program.to((CHIP_0002_SIGN_MESSAGE_PREFIX, message)).get_tree_hash()
    return SignMessageResponse(
        pubkey=public_key,
        signature=AugSchemeMPL.sign(secret_key, hex_message),
    )
```

**File:** chia/wallet/wallet_rpc_api.py (L1762-1781)
```python
    async def sign_message_by_address(self, request: SignMessageByAddress) -> SignMessageByAddressResponse:
        """
        Given a derived P2 address, sign the message by its private key.
        :param request:
        :return:
        """
        synthetic_secret_key = self.service.wallet_state_manager.main_wallet.convert_secret_key_to_synthetic(
            await self.service.wallet_state_manager.get_private_key(decode_puzzle_hash(request.address))
        )
        signing_response = sign_message(
            secret_key=synthetic_secret_key,
            message=request.message,
            mode=request.signing_mode_enum,
        )
        return SignMessageByAddressResponse(
            pubkey=signing_response.pubkey,
            signature=signing_response.signature,
            signing_mode=request.signing_mode_enum.value,
        )

```

**File:** chia/consensus/condition_tools.py (L140-145)
```python
    for cwa in conditions_dict.get(ConditionOpcode.AGG_SIG_UNSAFE, []):
        validate_cwa(cwa)
        for disallowed in data.values():
            if cwa.vars[1].endswith(disallowed):
                raise ConsensusError(Err.INVALID_CONDITION)
        ret.append((G1Element.from_bytes(cwa.vars[0]), cwa.vars[1]))
```
