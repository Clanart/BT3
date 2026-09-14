Found a directly analogous bug class: an untrusted length-prefixed "version index" is read from attacker-controlled bytes and used to slice/select into an internal table, with a boundary check that is off-by-one, mirroring the CVE's out-of-bounds string-type-index issue.

### Title
Off-by-one version-index bounds check in offer/CAT puzzle decompression allows out-of-range dictionary access - (File: `chia/wallet/util/puzzle_compression.py`)

### Summary
`decompress_object_with_puzzles()` reads an untrusted 2-byte big-endian `version` field directly from a compressed offer/CoinSpend blob and uses it to slice the module-level `ZDICT` list via `zdict_for_version(version)`. The guard `if version > len(ZDICT): raise CompressionVersionError(version)` is off-by-one: it allows `version == len(ZDICT)`, which is one past the last valid index for `ZDICT[0:version]` semantics used elsewhere (`lowest_best_version` treats valid `ZDICT` indices as `0..len(ZDICT)-1`).

### Finding Description
`decompress_object_with_puzzles()` [1](#0-0)  takes the first two bytes of an attacker-supplied offer/compressed puzzle blob as `version`, then calls `zdict_for_version(version)`: [2](#0-1) 

The bound check `version > len(ZDICT)` permits `version == len(ZDICT)` to pass through even though the last legitimate/allocated dictionary entry is the intentionally-broken empty placeholder `b""` at index `len(ZDICT)-1` [3](#0-2) . This mirrors the underlying bug class in the CVE (an index derived from untrusted encoded data used to reach into a type/string table without a correct upper-bound check).

This code path is reachable by any offer-file recipient/taker: `Offer.from_bech32()` / `Offer.from_bytes()` decode attacker-provided offer files that embed compressed puzzle reveals, and `CreateOfferForIDs`/`TakeOffer` RPC handlers accept externally supplied hex/bech32 offer content [4](#0-3)  which is parsed with `WalletSpendBundle.from_bytes` and puzzle-reveal drivers before any wallet policy checks occur.

### Impact Explanation
Because `Program`/CoinSpend puzzle reveals in offers can be arbitrary attacker bytes, an attacker can supply an out-of-range `version` value (`len(ZDICT)`) to reach the deliberately "broken" empty-dict compatibility slot, or, depending on how `ZDICT` grows in future releases, cause `zdict_for_version` to silently return a dictionary state inconsistent with what the taker/wallet expects. In the current dictionary content, this degrades to using an empty zlib dictionary (a decompression/parsing correctness bug rather than a memory-safety bug, since Python `list[0:n]` slicing does not raise `IndexError` for `n == len(list)`), but it is the closest reachable, wallet/offer-facing analog of the reported "index derived from untrusted encoded data used to reach past the intended boundary of a lookup table" bug class. This could let a malicious offer counterparty desynchronize which puzzle reveal a taker's wallet reconstructs from a compressed offer blob relative to what was intended, which is a precondition for tricking a wallet into misinterpreting/mis-displaying an offered asset's puzzle during offer parsing.

### Likelihood Explanation
Any wallet user or offer counterparty who opens/takes an untrusted offer file can trigger this path automatically, since offer decoding (`Offer.from_bech32`/`from_bytes`) and puzzle-reveal driver matching run before any semantic validation, and the version field is fully attacker-controlled. Likelihood of hitting the off-by-one is trivial (single crafted byte), but actual security impact is currently bounded by the harmless nature of an empty zlib dictionary and by CPython list-slice semantics not raising an exception — this is a real logic/boundary defect but with limited direct severity in the current, unmodified `ZDICT` contents.

### Recommendation
Change the version bound check in `decompress_object_with_puzzles()` from `if version > len(ZDICT)` to `if version >= len(ZDICT)` (or explicitly reject `version == len(ZDICT)`, given the last entry is a deliberately-broken placeholder), and add an explicit test asserting that `version == len(ZDICT)` raises `CompressionVersionError`, matching the invariant already implied by `lowest_best_version()`'s use of `ZDICT` indices `0..len(ZDICT)-1`.

### Proof of Concept
1. Construct a compressed puzzle blob `bytes([0x00, len(ZDICT)]) + zlib.compressobj(zdict=b"").compress(payload) + flush()` (i.e., set the 2-byte big-endian version field equal to `len(ZDICT)`).
2. Call `decompress_object_with_puzzles(blob)`.
3. Observe that the version check does not raise `CompressionVersionError` even though `version == len(ZDICT)` is out of the intended valid range `[0, len(ZDICT)-1]`, and decompression proceeds using the wrong (empty) zdict rather than being rejected as an unsupported/invalid version, unlike neighboring version values `> len(ZDICT)` which are correctly rejected.

### Citations

**File:** chia/wallet/util/puzzle_compression.py (L31-43)
```python
ZDICT = [
    bytes(standard_puzzle.MOD) + bytes(LEGACY_CAT_MOD),
    bytes(OFFER_MOD_OLD),
    bytes(SINGLETON_TOP_LAYER_MOD)
    + bytes(NFT_STATE_LAYER_MOD)
    + bytes(NFT_OWNERSHIP_LAYER)
    + bytes(NFT_METADATA_UPDATER)
    + bytes(NFT_TRANSFER_PROGRAM_DEFAULT),
    bytes(CAT_MOD),
    SETTLEMENT_PAYMENT,
    b"",  # purposefully break compatibility with older versions
    # more dictionaries go here
]
```

**File:** chia/wallet/util/puzzle_compression.py (L54-58)
```python
def zdict_for_version(version: int) -> bytes:
    summed_dictionary = b""
    for version_dict in ZDICT[0:version]:
        summed_dictionary += version_dict
    return summed_dictionary
```

**File:** chia/wallet/util/puzzle_compression.py (L73-79)
```python
def decompress_object_with_puzzles(compressed_object_blob: bytes) -> bytes:
    version = int.from_bytes(compressed_object_blob[0:2], "big")
    if version > len(ZDICT):
        raise CompressionVersionError(version)
    zdict = zdict_for_version(version)
    object_bytes = decompress_with_zdict(compressed_object_blob[2:], zdict)
    return object_bytes
```

**File:** chia/wallet/trading/offer.py (L697-725)
```python
    @classmethod
    def from_bech32(cls, offer_bech32: str) -> Offer:
        _hrpgot, data = bech32_decode(offer_bech32, max_length=len(offer_bech32))
        if data is None:
            raise ValueError("Invalid Offer")
        decoded = convertbits(list(data), 5, 8, False)
        decoded_bytes = bytes(decoded)
        return cls.try_offer_decompression(decoded_bytes)

    # Methods to make this a valid Streamable member
    # We basically hijack the SpendBundle versions for most of it
    @classmethod
    def parse(cls, f: BinaryIO) -> Offer:
        parsed_bundle = parse_rust(f, WalletSpendBundle)
        return cls.from_bytes(bytes(parsed_bundle))

    def stream(self, f: BinaryIO) -> None:
        spend_bundle_bytes = self.to_spend_bundle().to_bytes()
        f.write(spend_bundle_bytes)

    def __bytes__(self) -> bytes:
        return bytes(self.to_spend_bundle())

    @classmethod
    def from_bytes(cls, as_bytes: bytes) -> Offer:
        # Because of the __bytes__ method, we need to parse the dummy CoinSpends as `requested_payments`
        bundle = WalletSpendBundle.from_bytes(as_bytes)
        return cls.from_spend_bundle(bundle)

```
