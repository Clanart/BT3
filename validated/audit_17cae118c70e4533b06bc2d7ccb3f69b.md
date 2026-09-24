### Title
Out-of-bounds pointer traversal/corruption in `upb_inttable_trygrow` in-place map rehashing - (File: `upb/hash/common.c`, duplicated in `php/ext/google/protobuf/php-upb.c`, `ruby/ext/google/protobuf_c/ruby-upb.c`)

### Summary
The Sherlock report's failed invariant is: an index/state tracker (`ownerToRollOverQueueIndex`) is unconditionally overwritten to a value derived from *current* container length, without checking whether that value already correctly identifies an existing entry, allowing one user's action to silently corrupt another user's index and cause a wrong-entry removal. The transferable class of bug — "a bookkeeping/index value computed from current mutable container state is trusted without re-validating that it still points at the right logical entry, causing cross-entry corruption" — has a concrete memory-safety analog in upb's custom in-place hash-table growth routine `upb_inttable_trygrow`, used by every integer-keyed `upb_Map` (i.e. any `map<int32/int64/uint32/uint64/bool, ...>` protobuf field parsed through upb-backed bindings: PHP, Ruby, Python, and any C/C++ code using upb `Map` APIs directly). During the in-place rehash, some table slots are temporarily marked "not yet relocated" with a sentinel `next` pointer (`unhashed_marker = entries + new_size`, i.e. one-past-the-end). The generic `insert()` collision-chain-walking code, unaware of this sentinel convention, can dereference that sentinel as if it were a real `upb_tabent*`, walking off the end of the (in-place-extended) allocation.

### Finding Description
`upb_inttable_grow()` calls `upb_inttable_trygrow()` (`upb/hash/common.c:889-973`, byte-identical copies in the generated PHP/Ruby bundles) to grow a `upb_inttable` in place instead of allocating a fresh table: [1](#0-0) 

1. The buffer is extended in place and the new region zeroed.
2. A sentinel pointer one element past the new end of the array, `unhashed_marker = t->t.entries + new_size`, is used to tag every *occupied* old slot (`upb_tabent_setnext(e, unhashed_marker)`), destroying the original chain-`next` pointers before rehashing: [2](#0-1) 

3. `t->t.mask` and `t->t.count` are updated for the *new* size before all entries have actually been relocated, so the table is in an inconsistent, partially-rehashed state during the whole loop.
4. The trace/resolve loop walks each old slot; if that slot is still marked with `unhashed_marker`, it pops the key/val, then repeatedly computes the new hash bucket and either evicts a still-pending marked bucket (again via `insert()`) or calls the ordinary, marker-oblivious `insert()`: [3](#0-2) 

The bug is that the *generic* `insert()` routine (`upb/hash/common.c:178-233`, same duplicated in PHP/Ruby bundles) was never written to understand the `unhashed_marker` sentinel. When `insert()` is invoked mid-rehash to place an already-relocated (new-state) collision chain member, and that entry's own hash bucket happens to be occupied by a chain head (case: `mainpos_e` occupied, "existing ent not in its main position"), `insert()` recomputes `chain = getentry_mutable(t, hashfunc(mainpos_e->key, mainpos_e->val))` and then walks:

```c
while (upb_tabent_hasnext(chain) && upb_tabent_next(chain) != mainpos_e) {
  chain = upb_tabent_next(chain);
  UPB_ASSERT(chain);
}
upb_tabent_setnext(chain, new_e);
```

`upb_tabent_hasnext()` treats *any* non-`kUpb_NoNextTabent` pointer (including `unhashed_marker`) as "has next." If `chain` ever lands on, or is chained through, a slot still marked `unhashed_marker` (a physically distinct, valid-looking non-null pointer that is *not* `mainpos_e`), the loop condition is true, so it executes `chain = upb_tabent_next(chain);`, setting `chain = unhashed_marker = entries + new_size` — **one slot past the end of the allocated (in-place-extended) `upb_tabent` array**. The next loop iteration then calls `upb_tabent_hasnext(chain)`, which dereferences `chain->next`, i.e. reads memory immediately after the table's allocation: an out-of-bounds heap/arena read. `UPB_ASSERT(chain)` is a no-op in release/NDEBUG builds, so nothing stops execution. Depending on adjacent arena contents, this can (a) loop indefinitely walking garbage "next" pointers, (b) terminate on a coincidental match and then execute `upb_tabent_setnext(chain, new_e)` — an **out-of-bounds write** corrupting adjacent arena memory — and, in either case, silently produce a corrupted map (lost/duplicated/misassigned key-value entries), the direct functional analog of the Sherlock report's cross-entry corruption ("attacker can desync another entry's slot/index").

`findentry()` (`upb/hash/common.c:147-159`), used pervasively by `insert()`'s own `UPB_ASSERT(findentry(...) == NULL)` invariant check and other table operations, has the identical marker-oblivious traversal pattern and is equally exposed if a lookup/insert touches a chain containing a still-pending marked slot.

### Impact Explanation
This is reachable purely through ordinary, bounded Protobuf wire (or ProtoJSON) parsing of a trusted schema containing an integer-keyed `map<...>` field (`map<int32,...>`, `map<int64,...>`, `map<uint32,...>`, `map<uint64,...>`, `map<bool,...>`), via any upb-backed binding (PHP `mergeFromString`/`mergeFromJsonString`, Ruby, Python upb backend) or direct C/C++ use of `upb_Map`/`upb_inttable`. `upb_Map_New`/`_upb_Map_New` chooses `upb_inttable` for such key types: [4](#0-3) 

and `upb_Map_Insert`/`_upb_Map_Insert` funnels through `upb_inttable_insert` → `upb_inttable_grow` → `upb_inttable_trygrow` whenever the load factor threshold (0.875, `isfull()`) is crossed: [5](#0-4) 

Because the attacker fully controls the map's integer keys, and `upb_inthash` is a simple, non-cryptographic mix (`(uint32_t)key ^ (uint32_t)(key>>32)`), constructing keys that collide into the specific "evicted-chain lands on a still-pending marker slot" pattern during a single grow event is feasible with a small, bounded number of map entries (enough to trigger exactly one grow at a chosen table size). The resulting effect is memory-safety violation (OOB read, potentially OOB write/heap corruption) and/or data-integrity corruption of the parsed map — a severity consistent with High: it is attacker-triggerable with a small bounded input, requires no privileged access, and can corrupt program state or heap memory during otherwise-trusted parsing of an untrusted payload.

### Likelihood Explanation
Likely reachable in production because: (1) integer-keyed maps are common in real `.proto` schemas; (2) the growth threshold is a fixed 0.875 load factor on `upb_inttable`, so any sufficiently large or adversarially-sized map field will trigger `upb_inttable_grow`, which always tries `upb_inttable_trygrow` first; (3) the existing test suite (`upb/hash/test.cc`, `IntTableTest.InPlaceGrow`) only exercises "happy path" sequential-key growth with no crafted collisions, so the "chain evicts through a still-unhashed sentinel" case is untested and unguarded: [6](#0-5) 

The exact probability of hitting the corrupting collision pattern depends on `upb_inthash`'s bit-mixing versus chosen keys and table size at the moment of growth, which needs to be confirmed with a concrete reproduction (see PoC below) rather than asserted as certain — this is the main open uncertainty in this analysis, since a full dynamic trace through several nested `insert()` recursive collision-resolutions was reasoned about statically but not executed.

### Recommendation
Make `upb_inttable_trygrow`'s helper logic collision-safe with respect to the `unhashed_marker` sentinel: either (a) have `insert()`/`findentry()`'s chain-walking code explicitly detect and reject/handle `next == unhashed_marker` (never dereference or link through it), or (b) avoid reusing the generic `insert()` during the partially-rehashed state altogether — perform full extraction of *all* pending entries' key/value pairs first (independent of physical slot reuse), fully clear all old slots, and only then run standard `insert()` calls into a table guaranteed free of sentinel markers. Add a fuzz/unit test that constructs colliding integer keys designed to force chain eviction through a not-yet-relocated slot during `upb_inttable_trygrow`, and run it under ASan to confirm no OOB access occurs.

### Proof of Concept
A full reproduction requires selecting integer keys whose `upb_inthash` values collide against the *new* table mask in a way that (1) forces at least one key to remain in "unhashed pending" state at the physical slot that another, already-relocated key's main-position collision chain resolution needs to traverse through. This requires enumerating `upb_inthash(k) = (uint32_t)k ^ (uint32_t)(k>>32)` for candidate `uint64_t` keys against the specific `old_size`/`new_size` transition (e.g., growing from `old_size=8` to `new_size=16`) and simulating the `trace-and-resolve` loop offline to find a concrete colliding key set, then feed those keys as a `map<int64,int64>` field in a bounded serialized protobuf message to `upb_Decode` (or PHP/Ruby `mergeFromString`) under AddressSanitizer to confirm the OOB access at `upb/hash/common.c:967`/`:960` (`upb_tabent_next(chain)` dereference after `chain = unhashed_marker`). This concrete key-collision search and ASan run was not executed in this analysis — it is the necessary next verification step; the vulnerability is currently established through static code-path tracing of `upb_inttable_trygrow`/`insert()`/`findentry()`, not a completed dynamic reproduction.

### Citations

**File:** upb/hash/common.c (L906-930)
```c
  if (!upb_Arena_TryExtend(a, t->t.entries, old_bytes, new_bytes)) {
    return false;
  }

  // Zero out the newly extended region of the table buffer.
  memset(t->t.entries + old_size, 0,
         (new_size - old_size) * sizeof(upb_tabent));

  // This one-past-the-end pointer is guaranteed to be distinct from NULL and
  // any valid internal collision chain pointer in the entire table.
  upb_tabent* unhashed_marker = t->t.entries + new_size;

  // Mark all existing occupied entries as UNHASHED using the distinct
  // marker. Because e->next != NULL, these pending slots are fully protected
  // from being claimed by emptyent() during collision chain formation.
  for (size_t i = 0; i < old_size; i++) {
    upb_tabent* e = &t->t.entries[i];
    if (!upb_tabent_isempty(e)) {
      upb_tabent_setnext(e, unhashed_marker);
    }
  }

  const uint32_t mask = new_size - 1;
  t->t.mask = mask;
  t->t.count = 0;  // Reset count as insert will increment it back up
```

**File:** upb/hash/common.c (L932-972)
```c
  // Trace and resolve all destinations.
  for (size_t i = 0; i < old_size; i++) {
    upb_tabent* current = &t->t.entries[i];
    if (!upb_tabent_hasnext(current) ||
        upb_tabent_next(current) != unhashed_marker) {
      continue;  // Slot is already hashed or empty.
    }

    // Pop the leading unhashed entry to begin our trace cycle.
    upb_key tabkey = current->key;
    upb_value val = current->val;
    upb_tabent_clear(current);

    // This inner loop clears at least one unhashed slot per iteration, for
    // total O(n) time across the whole rehash.
    while (true) {
      uint32_t hash = inthash(tabkey, val);
      upb_tabent* target_bucket = &t->t.entries[hash & mask];

      if (upb_tabent_hasnext(target_bucket) &&
          upb_tabent_next(target_bucket) == unhashed_marker) {
        // Primary bucket contains a pending unhashed entry. Extract it, clear
        // the bucket, place our entry, and trace the evicted one.
        upb_key next_tabkey = target_bucket->key;
        upb_value next_val = target_bucket->val;

        upb_tabent_clear(target_bucket);

        insert(&t->t, intkey(tabkey.num), tabkey, val, hash, &inthash, &inteql);

        tabkey = next_tabkey;
        val = next_val;
      } else {
        // Primary bucket is either empty or holds an already-hashed chain.
        // The standard insert() function perfectly resolves both cases.
        insert(&t->t, intkey(tabkey.num), tabkey, val, hash, &inthash, &inteql);
        break;
      }
    }
  }
  return true;
```

**File:** upb/hash/common.c (L975-1002)
```c
UPB_NOINLINE static bool upb_inttable_grow(upb_inttable* t, upb_Arena* a) {
  size_t new_size = _upb_log2_table_size(&t->t) + 1;
  if (upb_inttable_trygrow(t, new_size, a)) return true;

  upb_table new_table;
  if (!init(&new_table, new_size, a)) return false;

  for (size_t i = begin(&t->t); i < upb_table_size(&t->t); i = next(&t->t, i)) {
    const upb_tabent* e = &t->t.entries[i];
    insert(&new_table, intkey(e->key.num), e->key, e->val,
           inthash(e->key, e->val), &inthash, &inteql);
  }

  UPB_ASSERT(t->t.count == new_table.count);
  t->t = new_table;
  return true;
}

bool upb_inttable_insert(upb_inttable* t, uintptr_t key, upb_value val,
                         upb_Arena* a) {
  if (UPB_UNLIKELY(isfull(&t->t))) {
    if (!upb_inttable_grow(t, a)) return false;
  }
  upb_key tabkey = {.num = key};
  insert(&t->t, intkey(key), tabkey, val, upb_inthash(key), &inthash, &inteql);
  check(t);
  return true;
}
```

**File:** upb/message/map.c (L179-195)
```c
upb_Map* _upb_Map_New(upb_Arena* a, size_t key_size, size_t value_size) {
  upb_Map* map = upb_Arena_Malloc(a, sizeof(upb_Map));
  if (!map) return NULL;

  if (key_size <= sizeof(uintptr_t) && key_size != UPB_MAPTYPE_STRING) {
    if (!upb_inttable_init(&map->t.inttable, a)) return NULL;
    map->UPB_PRIVATE(is_strtable) = false;
  } else {
    if (!upb_strtable_init(&map->t.strtable, 4, a)) return NULL;
    map->UPB_PRIVATE(is_strtable) = true;
  }
  map->key_size = key_size;
  map->val_size = value_size;
  map->UPB_PRIVATE(is_frozen) = false;

  return map;
}
```

**File:** upb/hash/test.cc (L640-662)
```text
TEST(IntTableTest, InPlaceGrow) {
  char initial_buf[65536];
  upb_Arena* arena = upb_Arena_Init(initial_buf, sizeof(initial_buf), nullptr);
  upb_inttable t;
  ASSERT_TRUE(upb_inttable_init(&t, arena));

  const void* initial_entries = t.t.entries;
  for (uintptr_t i = 1; i <= 100; i++) {
    ASSERT_TRUE(upb_inttable_insert(&t, i, upb_value_uint64(i * 10), arena));
  }
  // Verify that in-place growth actually occurred (entries pointer unchanged).
  EXPECT_EQ(t.t.entries, initial_entries);
  EXPECT_EQ(upb_inttable_count(&t), 100);

  for (uintptr_t i = 1; i <= 100; i++) {
    upb_value val;
    ASSERT_TRUE(upb_inttable_lookup(&t, i, &val))
        << "Failed lookup for key " << i;
    EXPECT_EQ(upb_value_getuint64(val), i * 10);
  }

  upb_Arena_Free(arena);
}
```
