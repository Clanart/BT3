## Analog Identified: ASLR-Derived, Attacker-Enumerable Hash Seed Drives Observable Non-Deterministic Map/Extension Ordering in upb

### Title
Information Exposure via Attacker-Enumerable Process ASLR Offset Through upb's Address-Derived Hash Seed - (File: `upb/hash/common.c`)

### Summary
CVE-2019-10639 shows a KASLR bypass where a per-boot secret hashing key (embedding kernel address bits) is reused across all IP-ID computations, letting a remote attacker enumerate the key via observed hash-bucket collisions across many attacker-triggered probes. The invariant that fails there is: *a secret derived from a process/kernel address is reused indefinitely and its effects (bucket/index selection) are externally observable across many attacker-chosen inputs, allowing statistical enumeration of the secret and thus the address offset.*

The closest structural analog in this Protobuf/upb checkout is `_upb_Seed()`, which derives the hash-table seed used for **every** `upb_Map` and extension table lookup/iteration from the address of a static process-lifetime variable, and this seed is reused for the entire process lifetime and directly determines the *externally observable* bucket ordering exposed via non-deterministic serialization/text-encoding of attacker-controlled map keys. [1](#0-0) 

### Finding Description
`_upb_Seed()` returns `(uint64_t)&_upb_seed`, i.e., the address of a static variable, explicitly to get "some randomness for free provided that ASLR is enabled." This value is used unconditionally as the seed for `_upb_Hash_NoSeed`, which backs `strhash` for all `upb_strtable`-based `upb_Map` instances, and analogous code paths hash extension numbers combined with the same seed via `_upb_exttable_hash`. [2](#0-1) [3](#0-2) 

Critically, this same seed derivation is duplicated verbatim in the PHP and Ruby native extensions (bundled `upb` copies), so the exposure applies across bindings that use upb as their wire/JSON backend: [4](#0-3) [5](#0-4) 

The bucket that a given map key lands in is `hash & mask` (`upb_getentry`), so the seed directly controls bucket placement: [6](#0-5) 

That bucket placement becomes externally observable whenever a message containing a client-supplied map is serialized/printed **without** deterministic mode — the default for `Serialize()`/text encoding walks the raw hash table in bucket order rather than sorted order: [7](#0-6) [8](#0-7) 

Compare this to the deterministic path, which explicitly sorts and thus hides the hash-table layout — confirming the library authors are aware ordering is otherwise observable and only suppress it when `kUpb_EncodeOption_Deterministic`/`UPB_TXTENC_NOSORT` handling is bypassed: [9](#0-8) 

**Failed invariant that transfers:** a secret/address-derived value is (a) reused for the lifetime of the process across unboundedly many attacker-chosen inputs, and (b) its effect on output (bucket index / relative ordering of two colliding keys) is directly observable by the party supplying the inputs. In the kernel CVE this enabled enumeration of the secret and thus a KASLR offset; here it enables enumeration of `_upb_Seed()`'s value and thus the ASLR slide of the module containing `_upb_seed`, precisely the same class of side channel (CWE-203 Observable Discrepancy / partial address disclosure).

**Missing check:** there is no periodic seed rotation, no cryptographic (SipHash-class, unpredictable-per-process-with-high-entropy) hashing, and no restriction preventing an attacker from supplying many distinct map keys and observing their relative serialized order across repeated round-trips through a consuming application that echoes the serialized/printed representation back (e.g., an RPC that stores-and-returns a message, a proxy, or a debug/logging endpoint using `TextFormat`/`upb_text_encode` on attacker data).

### Impact Explanation
An attacker who can (1) submit protobuf messages containing attacker-chosen map keys through a supported parse API, and (2) observe the non-deterministic serialized byte order or text-encoded order of that map (a common pattern for echo/proxy/logging services, or via `DebugString`), can, over repeated queries, detect bucket collisions between different key sets. Because the seed is the address of a fixed static variable reused for the entire process lifetime, enough observed collisions allow statistical narrowing of `_upb_Seed()`'s value — directly revealing bits of the ASLR base of the shared object/binary containing `upb`'s static data, analogous in kind to the KASLR offset disclosure in the CVE. This does not itself grant code execution, but it degrades an ASLR-dependent memory-safety mitigation for the host application, exactly as CVE-2019-10639 degraded KASLR without independently causing a crash.

### Likelihood Explanation
Exploitation requires: a consuming application that (a) accepts attacker-controlled map keys via public Protobuf/ProtoJSON parse APIs (trusted schema, bounded payload — consistent with rules), (b) serializes/prints that message using the non-deterministic default path, and (c) exposes that byte/text ordering back to the same attacker across many repeated interactions. These are realistic but non-trivial preconditions (statistical side-channel requiring many probes, not a single-request exploit), which places this at Medium rather than High likelihood; it is also explicitly a "for free" convenience mechanism the authors documented as *not* a security defense, so no compensating control currently exists in-tree.

### Recommendation
- Do not derive `_upb_Seed()` from the address of a static variable when any code path can expose non-deterministic map/extension ordering to values that ultimately reach parties who supplied the map keys. Use a securely-generated, per-process random seed (e.g., from a CSPRNG) that carries no relationship to process memory layout, matching the hardening already applied to Abseil's own hash salt.
- Alternatively/additionally, document and enforce that any code path echoing attacker-supplied message content back to the origin of that content (logging, echo services, proxies) should default to `kUpb_EncodeOption_Deterministic` / `UPB_TXTENC_NOSORT` disabled (i.e., sorted mode), removing the observable side channel entirely.
- Rotate/re-derive the seed periodically rather than reusing one fixed value for the full process lifetime, breaking the "unlimited free enumeration attempts" precondition that made the original kernel attack practical.

### Proof of Concept
Conceptual reproduction (bounded, trusted-schema, public-API only):
1. Trusted schema defines `message M { map<string, int32> m = 1; }`.
2. Attacker repeatedly sends distinct `M` messages with attacker-chosen string keys through the public `ParseFromString`/`upb_Decode` API of a consuming service.
3. The service serializes the parsed `M` back with the default (non-deterministic) `Serialize()`/`upb_Encode()` (no `kUpb_EncodeOption_Deterministic`) and returns the bytes/text to the attacker, as several real-world echo/log/relay services do — see the non-sorted path taken unless deterministic option is explicitly set: [10](#0-9) .
4. Because bucket index is `_upb_Hash(key, _upb_Seed()) & mask` with the same process-lifetime `_upb_Seed()` for every request [11](#0-10) , the attacker records which pairs of keys collide into the same/adjacent bucket order across repeated calls and statistically narrows `_upb_Seed()`'s value, thereby recovering bits of that binary's ASLR slide — the direct analog of enumerating the kernel's per-namespace secret to obtain the KASLR offset.

No test execution was performed; this is a structural/code-path analysis based on the cited source. Confirming actual exploitability end-to-end (bucket-count needed for practical enumeration, statistical feasibility) would require building a harness around `upb_Map`/`upb_Encode` with a fixed process and measuring achievable collision resolution — recommended as a follow-up before treating this as more than a theoretical/Medium-severity information-exposure finding.

### Citations

**File:** upb/hash/common.c (L427-532)
```c
static uint64_t WyhashMix(uint64_t v0, uint64_t v1) {
  uint64_t high;
  uint64_t low = upb_umul128(v0, v1, &high);
  return low ^ high;
}

static uint64_t Wyhash(const void* data, size_t len, uint64_t seed,
                       const uint64_t salt[]) {
  const uint8_t* ptr = (const uint8_t*)data;
  uint64_t starting_length = (uint64_t)len;
  uint64_t current_state = seed ^ salt[0];

  if (len > 64) {
    // If we have more than 64 bytes, we're going to handle chunks of 64
    // bytes at a time. We're going to build up two separate hash states
    // which we will then hash together.
    uint64_t duplicated_state = current_state;

    do {
      uint64_t a = UnalignedLoad64(ptr);
      uint64_t b = UnalignedLoad64(ptr + 8);
      uint64_t c = UnalignedLoad64(ptr + 16);
      uint64_t d = UnalignedLoad64(ptr + 24);
      uint64_t e = UnalignedLoad64(ptr + 32);
      uint64_t f = UnalignedLoad64(ptr + 40);
      uint64_t g = UnalignedLoad64(ptr + 48);
      uint64_t h = UnalignedLoad64(ptr + 56);

      uint64_t cs0 = WyhashMix(a ^ salt[1], b ^ current_state);
      uint64_t cs1 = WyhashMix(c ^ salt[2], d ^ current_state);
      current_state = (cs0 ^ cs1);

      uint64_t ds0 = WyhashMix(e ^ salt[3], f ^ duplicated_state);
      uint64_t ds1 = WyhashMix(g ^ salt[4], h ^ duplicated_state);
      duplicated_state = (ds0 ^ ds1);

      ptr += 64;
      len -= 64;
    } while (len > 64);

    current_state = current_state ^ duplicated_state;
  }

  // We now have a data `ptr` with at most 64 bytes and the current state
  // of the hashing state machine stored in current_state.
  while (len > 16) {
    uint64_t a = UnalignedLoad64(ptr);
    uint64_t b = UnalignedLoad64(ptr + 8);

    current_state = WyhashMix(a ^ salt[1], b ^ current_state);

    ptr += 16;
    len -= 16;
  }

  // We now have a data `ptr` with at most 16 bytes.
  uint64_t a = 0;
  uint64_t b = 0;
  if (len > 8) {
    // When we have at least 9 and at most 16 bytes, set A to the first 64
    // bits of the input and B to the last 64 bits of the input. Yes, they will
    // overlap in the middle if we are working with less than the full 16
    // bytes.
    a = UnalignedLoad64(ptr);
    b = UnalignedLoad64(ptr + len - 8);
  } else if (len > 3) {
    // If we have at least 4 and at most 8 bytes, set A to the first 32
    // bits and B to the last 32 bits.
    a = UnalignedLoad32(ptr);
    b = UnalignedLoad32(ptr + len - 4);
  } else if (len > 0) {
    // If we have at least 1 and at most 3 bytes, read all of the provided
    // bits into A, with some adjustments.
    a = ((ptr[0] << 16) | (ptr[len >> 1] << 8) | ptr[len - 1]);
    b = 0;
  } else {
    a = 0;
    b = 0;
  }

  uint64_t w = WyhashMix(a ^ salt[1], b ^ current_state);
  uint64_t z = salt[1] ^ starting_length;
  return WyhashMix(w, z);
}

const uint64_t kWyhashSalt[5] = {
    0x243F6A8885A308D3ULL, 0x13198A2E03707344ULL, 0xA4093822299F31D0ULL,
    0x082EFA98EC4E6C89ULL, 0x452821E638D01377ULL,
};

uint32_t _upb_Hash(const void* p, size_t n, uint64_t seed) {
  return Wyhash(p, n, seed, kWyhashSalt);
}

static const void* const _upb_seed;

// Returns a random seed for upb's hash function. This does not provide
// high-quality randomness, but it should be enough to prevent unit tests from
// relying on a deterministic map ordering. By returning the address of a
// variable, we are able to get some randomness for free provided that ASLR is
// enabled.
static uint64_t _upb_Seed(void) { return (uint64_t)&_upb_seed; }

static uint32_t _upb_Hash_NoSeed(const char* p, size_t n) {
  return _upb_Hash(p, n, _upb_Seed());
}
```

**File:** upb/hash/common.c (L719-725)
```c
/* upb_exttable ***************************************************************/

static uint32_t _upb_exttable_hash(const void* ptr, uint32_t ext_num) {
  uint64_t a = (uintptr_t)ptr;
  uint64_t b = ext_num;
  return (uint32_t)WyhashMix(a ^ kWyhashSalt[1], b ^ _upb_Seed());
}
```

**File:** php/ext/google/protobuf/php-upb.c (L3992-3994)
```c
static upb_tabent* upb_getentry(const upb_table* t, uint32_t hash) {
  return t->entries + (hash & t->mask);
}
```

**File:** php/ext/google/protobuf/php-upb.c (L4405-4425)
```c
const uint64_t kWyhashSalt[5] = {
    0x243F6A8885A308D3ULL, 0x13198A2E03707344ULL, 0xA4093822299F31D0ULL,
    0x082EFA98EC4E6C89ULL, 0x452821E638D01377ULL,
};

uint32_t _upb_Hash(const void* p, size_t n, uint64_t seed) {
  return Wyhash(p, n, seed, kWyhashSalt);
}

static const void* const _upb_seed;

// Returns a random seed for upb's hash function. This does not provide
// high-quality randomness, but it should be enough to prevent unit tests from
// relying on a deterministic map ordering. By returning the address of a
// variable, we are able to get some randomness for free provided that ASLR is
// enabled.
static uint64_t _upb_Seed(void) { return (uint64_t)&_upb_seed; }

static uint32_t _upb_Hash_NoSeed(const char* p, size_t n) {
  return _upb_Hash(p, n, _upb_Seed());
}
```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L3158-3178)
```c
const uint64_t kWyhashSalt[5] = {
    0x243F6A8885A308D3ULL, 0x13198A2E03707344ULL, 0xA4093822299F31D0ULL,
    0x082EFA98EC4E6C89ULL, 0x452821E638D01377ULL,
};

uint32_t _upb_Hash(const void* p, size_t n, uint64_t seed) {
  return Wyhash(p, n, seed, kWyhashSalt);
}

static const void* const _upb_seed;

// Returns a random seed for upb's hash function. This does not provide
// high-quality randomness, but it should be enough to prevent unit tests from
// relying on a deterministic map ordering. By returning the address of a
// variable, we are able to get some randomness for free provided that ASLR is
// enabled.
static uint64_t _upb_Seed(void) { return (uint64_t)&_upb_seed; }

static uint32_t _upb_Hash_NoSeed(const char* p, size_t n) {
  return _upb_Hash(p, n, _upb_Seed());
}
```

**File:** upb/wire/internal/encoder.c (L595-643)
```c
UPB_NOINLINE
static char* encode_map(char* ptr, upb_encstate* e, const upb_Message* msg,
                        const upb_MiniTableField* f) {
  const upb_Map* map = *UPB_PTR_AT(msg, f->UPB_PRIVATE(offset), const upb_Map*);
  const upb_MiniTable* layout = upb_MiniTable_MapEntrySubMessage(f);
  UPB_ASSERT(upb_MiniTable_FieldCount(layout) == 2);

  if (!map || !upb_Map_Size(map)) return ptr;

  uint32_t number = upb_MiniTableField_Number(f);
  uint32_t key_size = map->key_size;
  uint32_t val_size = map->val_size;
  const upb_MiniTableField* key_field = upb_MiniTable_MapKey(layout);
  const upb_MiniTableField* val_field = upb_MiniTable_MapValue(layout);
  if (e->options & kUpb_EncodeOption_Deterministic) {
    _upb_sortedmap sorted;
    if (!_upb_mapsorter_pushmap(
            &e->sorter, key_field->UPB_PRIVATE(descriptortype), map, &sorted)) {
      encode_err(e, kUpb_EncodeStatus_OutOfMemory);
    }
    upb_MapEntry ent;
    while (_upb_sortedmap_next(&e->sorter, map, &sorted, &ent)) {
      ptr = encode_mapentry(ptr, e, number, key_field, val_field, &ent);
    }
    _upb_mapsorter_popmap(&e->sorter, &sorted);
  } else {
    upb_value val;
    if (map->UPB_PRIVATE(is_strtable)) {
      intptr_t iter = UPB_STRTABLE_BEGIN;
      upb_StringView strkey;
      while (upb_strtable_next2(&map->t.strtable, &strkey, &val, &iter)) {
        upb_MapEntry ent;
        _upb_map_fromkey(strkey, &ent.k, key_size);
        _upb_map_fromvalue(val, &ent.v, val_size);
        ptr = encode_mapentry(ptr, e, number, key_field, val_field, &ent);
      }
    } else {
      intptr_t iter = UPB_INTTABLE_BEGIN;
      uintptr_t intkey = 0;
      while (upb_inttable_next(&map->t.inttable, &intkey, &val, &iter)) {
        upb_MapEntry ent;
        UPB_ASSUME(key_size != UPB_MAPTYPE_STRING);
        memcpy(&ent.k, &intkey, key_size);
        _upb_map_fromvalue(val, &ent.v, val_size);
        ptr = encode_mapentry(ptr, e, number, key_field, val_field, &ent);
      }
    }
  }
  return ptr;
```

**File:** upb/text/encode.c (L139-146)
```c
static void _upb_TextEncode_Map(txtenc* e, const upb_Map* map,
                                const upb_FieldDef* f) {
  if (e->options & UPB_TXTENC_NOSORT) {
    size_t iter = kUpb_Map_Begin;
    upb_MessageValue key, val;
    while (upb_Map_Next(map, &key, &val, &iter)) {
      _upb_TextEncode_MapEntry(e, key, val, f);
    }
```

**File:** upb/message/map_sorter.c (L125-155)
```c
bool _upb_mapsorter_pushmap(_upb_mapsorter* s, upb_FieldType key_type,
                            const upb_Map* map, _upb_sortedmap* sorted) {
  int map_size = _upb_Map_Size(map);

  if (!_upb_mapsorter_resize(s, sorted, map_size)) return false;

  // Copy non-empty entries from the table to s->entries.
  const void** dst = &s->entries[sorted->start];
  const upb_tabent* src;
  const upb_tabent* end;
  if (map->UPB_PRIVATE(is_strtable)) {
    src = map->t.strtable.t.entries;
    end = src + upb_table_size(&map->t.strtable.t);
  } else {
    src = map->t.inttable.t.entries;
    end = src + upb_table_size(&map->t.inttable.t);
  }
  for (; src < end; src++) {
    if (!upb_tabent_isempty(src)) {
      *dst = src;
      dst++;
    }
  }
  UPB_ASSERT(dst == &s->entries[sorted->end]);

  // Sort entries according to the key type.
  qsort(&s->entries[sorted->start], map_size, sizeof(*s->entries),
        map->UPB_PRIVATE(is_strtable) ? compar[key_type]
                                      : _upb_mapsorter_intkeys);
  return true;
}
```
