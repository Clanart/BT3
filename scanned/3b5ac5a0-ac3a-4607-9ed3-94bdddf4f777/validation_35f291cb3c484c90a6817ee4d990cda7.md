Based on the code I was able to fully verify in `ledger/src/bit_vec.rs`, I can present a concrete analog. I was not able to fully confirm every downstream consumer of this type within the remaining iterations (grep showed 6 references to `BitVec`/`bit_vec` in `ledger/src/blockstore_meta.rs`, but I could not read those lines before running out of tool calls), so I flag that as an open item below.

### Title
`BitVec<NUM_BITS>` derives `PartialEq`/`Eq` on raw, unmasked deserialized tail bits, causing logically-identical bit sets to compare unequal - (File: ledger/src/bit_vec.rs)

### Summary
`BitVec<NUM_BITS>` stores its bits in a `Box<[Word]>` and derives `PartialEq, Eq` directly on that raw byte array [1](#0-0) . Its custom `SchemaRead` implementation only guarantees the deserialized `words` vector has exactly `NUM_WORDS` entries (padding/truncating whole words), but it never masks or validates the "tail" bits of the final word that lie beyond the logical `NUM_BITS` boundary when `NUM_BITS` is not a multiple of 8 [2](#0-1) . This is the exact analog of the `_isZeroBytes` bug: the code assumes the trailing, logically-unused bits are always zero, but nothing enforces that on data coming from untrusted, externally-serialized input.

### Finding Description
The author was clearly aware that deserialized data can carry non-zero tail bits, and patched `next_set_bit`/`prev_set_bit` to explicitly ignore any hit beyond `NUM_BITS` via an `in_bounds` guard [3](#0-2) , and there's even a regression test documenting this exact scenario ("NUM_BITS=12 leaves a 4-bit tail in the final word. Deserialization does not mask tail bits...") [4](#0-3) .

However, this masking was never applied to the derived `PartialEq`/`Eq` implementation, which compares the `words: Box<[Word]>` field byte-for-byte [1](#0-0) . Two `BitVec<NUM_BITS>` values that are logically identical — i.e., they set exactly the same bits within `[0, NUM_BITS)` — can therefore compare as `!=` if one of them was produced by deserializing attacker/peer-controlled bytes whose final word has non-zero bits at positions `>= NUM_BITS`, while the other was built locally via `insert`/`insert_unchecked`/`FromIterator` (which can only ever set bits within `[0, NUM_BITS)`, so a locally constructed instance always has zeroed tail bits).

The corrupted value is the tail-bit portion of the last `Word` in `words`, which is populated straight from the wire via `<Vec<u8> as SchemaRead<C>>::get(...)` with no bit-masking step, unlike the already-hardened iteration paths (`next_set_bit`, `prev_set_bit`, `range`/`compute_word_mask`, which all clamp against `NUM_BITS`) [5](#0-4) [6](#0-5) .

### Impact Explanation
Any consumer that relies on structural equality of a `BitVec<NUM_BITS>` (e.g. comparing a network/ledger-deserialized value against a locally-constructed canonical value, or as part of an enclosing struct's derived `PartialEq` used for de-duplication, "already seen" checks, or consistency assertions) can be tricked by a remote peer into producing spurious inequality, even though the visible/semantic bit content is identical. Depending on where this equality check gates behavior (e.g., duplicate-shred/erasure-meta bookkeeping in the blockstore, which is where `BitVec`/`bit_vec` symbols also appear per `ledger/src/blockstore_meta.rs`), this could let an attacker bypass a dedup/consistency check that assumes equal bit-sets are always compared equal, causing repeated/duplicated processing of otherwise-identical structures. I was unable to confirm the exact blockstore consumer and its security role within the available tool budget, so this impact chain is only partially confirmed by local evidence.

### Likelihood Explanation
The mechanism itself requires no privilege — it only requires the ability to submit serialized bytes that get deserialized into a `BitVec<NUM_BITS>` where `NUM_BITS % 8 != 0`, which is trivial for any unprivileged peer since `SchemaRead` performs no validation of the tail bits [2](#0-1) . The likelihood of this being *exploitable to a listed impact* depends entirely on how the enclosing consumer uses `==`/derived-`PartialEq` on this type, which I could not fully verify.

### Recommendation
Mask tail bits immediately after deserialization in the `SchemaRead` impl (zero out bits `>= NUM_BITS` in the final word before storing), so that the invariant "all bits beyond `NUM_BITS` are always zero" holds unconditionally for every `BitVec<NUM_BITS>` instance, matching what the already-hardened `next_set_bit`/`prev_set_bit`/`range` code paths assume. Alternatively, replace the derived `PartialEq`/`Eq` with a manual implementation that only compares the masked, in-bounds bit range.

### Proof of Concept
```rust
// NUM_BITS=12 -> NUM_WORDS = 2 (Word = u8), last word has 4 "tail" bits (indices 12..16)
let mut deserialized = BitVec::<12>::default();
deserialized.words[1] = 0xF0; // simulate attacker-controlled trailing garbage from the wire,
                               // as done in the existing regression test at bit_vec.rs:708-719

let local = BitVec::<12>::default(); // constructed locally, tail bits always zero

// Logically both are "empty" (no bit in [0,12) is set) — confirmed by iter_ones()/contains()
assert!(deserialized.iter_ones().collect::<Vec<_>>().is_empty());
assert!(local.iter_ones().collect::<Vec<_>>().is_empty());

// But structural equality (derived PartialEq) disagrees:
assert_ne!(deserialized, local); // returns true: `!=` even though semantically identical
```
This mirrors the Linea `_isZeroBytes` flaw: logic that inspects only the "meaningful" range (`iter_ones`, `contains`, `next_set_bit`) correctly ignores the extra bytes/bits, while a different code path (here, derived `==`) naively compares the full underlying buffer, producing a different result for the same logical input.

### Citations

**File:** ledger/src/bit_vec.rs (L33-36)
```rust
#[derive(Debug, Clone, PartialEq, Eq, SchemaWrite)]
pub struct BitVec<const NUM_BITS: usize> {
    words: Box<[Word]>,
}
```

**File:** ledger/src/bit_vec.rs (L82-93)
```rust
unsafe impl<'de, const NUM_BITS: usize, C: Config> SchemaRead<'de, C> for BitVec<NUM_BITS> {
    type Dst = Self;

    fn read(mut reader: impl Reader<'de>, dst: &mut MaybeUninit<Self::Dst>) -> ReadResult<()> {
        let mut vec = <Vec<u8> as SchemaRead<C>>::get(reader.by_ref())?;
        vec.resize(Self::NUM_WORDS, 0);
        dst.write(Self {
            words: vec.into_boxed_slice(),
        });
        Ok(())
    }
}
```

**File:** ledger/src/bit_vec.rs (L339-352)
```rust
    pub fn next_set_bit(&self, from: usize) -> Option<usize> {
        if from >= NUM_BITS {
            return None;
        }
        // Serialized data may contain non-zero tail bits past NUM_BITS. Match
        // the range iterator by ignoring any hit outside the logical bit length.
        let in_bounds = |pos: usize| (pos < NUM_BITS).then_some(pos);
        let (first_word_idx, first_bit) = location_of(from);
        let first_word = self.words[first_word_idx] & (Word::MAX << first_bit);
        if first_word != 0 {
            return in_bounds(
                first_word_idx * BITS_PER_WORD + first_word.trailing_zeros() as usize,
            );
        }
```

**File:** ledger/src/bit_vec.rs (L480-504)
```rust
    fn from_range_bounds(bit_vec: &'a [u8], bounds: impl RangeBounds<usize>) -> Self {
        let start = match bounds.start_bound() {
            Bound::Included(&n) => n,
            Bound::Excluded(&n) => n + 1,
            Bound::Unbounded => 0,
        }
        .min(NUM_BITS);
        let end = match bounds.end_bound() {
            Bound::Included(&n) => n + 1,
            Bound::Excluded(&n) => n,
            Bound::Unbounded => NUM_BITS,
        }
        .min(NUM_BITS);
        let end_word: usize = end.div_ceil(BITS_PER_WORD);
        let start_word = (start / BITS_PER_WORD).min(end_word);

        Self {
            mask_iter: BitVecMaskIter {
                start,
                end,
                start_word,
                iter: bit_vec[start_word..end_word].iter().enumerate(),
            },
        }
    }
```

**File:** ledger/src/bit_vec.rs (L707-719)
```rust
    #[test]
    fn test_next_set_bit_ignores_final_word_tail_bits() {
        // NUM_BITS=12 leaves a 4-bit tail in the final word. Deserialization does not
        // mask tail bits, so the scan must ignore them like the iterator path does.
        let mut bv = BitVec::<12>::default();
        bv.words[1] = 0xF0;
        assert_eq!(bv.iter_ones().next(), None);
        assert_eq!(bv.next_set_bit(0), None);
        assert_eq!(bv.next_set_bit(11), None);
        bv.insert_unchecked(11);
        assert_eq!(bv.next_set_bit(0), Some(11));
        assert_eq!(bv.prev_set_bit(12), Some(11));
    }
```
