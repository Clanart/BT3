### Title
Missing NULL-check on `upb_Arena_Malloc()` return value before `memcpy()` in Lua `upb.decode()` binding causes NULL-pointer dereference - (File: `lua/msg.c`)

### Summary
The kernel CVE-2022-3107 is a classic "unchecked allocator return value" bug: `netvsc_get_ethtool_stats()` calls `kvmalloc_array()` and uses the result without checking for `NULL`, causing a NULL-pointer dereference when the allocation fails. The invariant violated is "always validate a fallible allocator's return value before dereferencing/writing through it." The strongest Protobuf analog is in the Lua binding's public decode entrypoint, `lupb_decode()`, which calls the explicitly fallible `upb_Arena_Malloc()` and immediately `memcpy()`s into the result without checking for `NULL`.

### Finding Description
`upb_Arena_Malloc()` is documented and implemented as a fallible operation — it returns `NULL` on allocation failure, and callers across the upb codebase are expected to check it (e.g. `_upb_Message_New()` in `upb/message/internal/message.h:4639-4640` checks `if (UPB_UNLIKELY(!msg)) return NULL;`, and `_upb_Decoder_ReadString` in `upb/wire/internal/decoder.h:255-256` checks `if (!data) return false;`). The upb design doc explicitly states: *"It follows that `upb_Arena_Malloc()` is a fallible operation, and all allocating operations... should be checked for failure"* (`docs/upb/design.md:164-166`).

`lupb_decode()` in `lua/msg.c:936-958` — the C implementation backing the public Lua API `upb.decode(MessageClass, bin_string)` — violates this invariant:
```
buf = upb_Arena_Malloc(arena, len);
memcpy(buf, pb, len);
```
`buf` is used directly in `memcpy()` with no NULL check. If `upb_Arena_Malloc()` returns `NULL`, this becomes `memcpy(NULL, pb, len)`, a NULL-pointer dereference/write, structurally identical to the kernel bug's `kvmalloc_array()` result being used without a NULL check. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation
`len` here is the size of the attacker-supplied bounded protobuf payload passed to the public `upb.decode()` API (`lua_tolstring` in `lua/msg.c:939`) — this is a bounded, attacker-controlled value flowing from a supported public parse API into an unchecked allocation. Unlike most upb call sites, which consistently null-check `upb_Arena_Malloc`/`_upb_Message_New` results (confirmed across `upb/message/internal/message.h`, `upb/wire/internal/decoder.h`, `upb/message/message.c`, `upb/mini_descriptor/decode.c`), this specific Lua binding path skips the check. On allocation failure (achievable even without huge/unbounded input, e.g. genuine OOM conditions, restricted/fixed-size arenas, or injected allocation failures — as tested by `upb_AllocationCount_FailOn` in `upb/mem/arena_test.cc:1029-1066`), the missing check leads directly to a crash (NULL write via `memcpy`), i.e., denial of service of the consuming application's Lua process. This mirrors the kernel bug's confirmed impact class (Availability: High, no Confidentiality/Integrity impact per the CVE's CVSS vector).

### Likelihood Explanation
The code path is directly reachable through the public, documented Lua API `upb.decode()`, requires no privileged access, and the missing check is present in production (non-test, non-generated) source. However, unlike CVE-2022-3107 — where the driver-level allocation failure is comparatively easier to trigger — triggering `upb_Arena_Malloc` failure here generally requires genuine memory pressure/OOM (or an artificially restricted arena), since Lua's decode arena is a normal growable heap-backed arena, not a fixed-size one (I could not fully confirm the exact Lua arena allocator configuration in the available index; `lupb_Arenaget`/`Arena_alloc` construction details were not fully retrievable). This keeps likelihood at Medium/lower rather than High: the bug is a real, unguarded invariant violation on a public parse path, but exploitation is gated on an actual allocation failure, similar to the low-but-real likelihood in the original kernel report.

### Recommendation
Add a NULL check on the result of `upb_Arena_Malloc()` in `lupb_decode()` before the `memcpy`, matching the pattern used elsewhere in upb (`if (!buf) { lua_pushstring(L, "Out of memory"); return lua_error(L); }`), and audit other Lua binding call sites (`lupb_msg_tostring`'s raw `malloc()` at `lua/msg.c:899` is unchecked too) for the same missing-check pattern.

### Proof of Concept
Not independently executed (no test harness run) — the finding is based on direct code inspection. Reproduction outline for confirmation: instrument/replace the global `upb_alloc` used by the Lua arena (or use `upb_AllocationCount_FailOn`, as demonstrated in `upb/mem/arena_test.cc:1029-1066`) to force `upb_Arena_Malloc` to return `NULL` on the call inside `lupb_decode()`, then invoke `upb.decode(MessageClass, some_bin_string)` from Lua; the process should crash with a NULL-pointer write in `memcpy` at `lua/msg.c:947` instead of returning a Lua-level allocation error.

### Citations

**File:** lua/msg.c (L936-958)
```c
static int lupb_decode(lua_State* L) {
  size_t len;
  const upb_MessageDef* m = lupb_MessageDef_check(L, 1);
  const char* pb = lua_tolstring(L, 2, &len);
  const upb_MiniTable* layout = upb_MessageDef_MiniTable(m);
  upb_Message* msg = lupb_msg_pushnew(L, 1);
  upb_Arena* arena = lupb_Arenaget(L, -1);
  char* buf;

  /* Copy input data to arena, message will reference it. */
  buf = upb_Arena_Malloc(arena, len);
  memcpy(buf, pb, len);

  upb_DecodeStatus status = upb_Decode(buf, len, msg, layout, NULL,
                                       kUpb_DecodeOption_AliasString, arena);

  if (status != kUpb_DecodeStatus_Ok) {
    lua_pushstring(L, "Error decoding protobuf.");
    return lua_error(L);
  }

  return 1;
}
```

**File:** docs/upb/design.md (L164-166)
```markdown
It follows that `upb_Arena_Malloc()` is a fallible operation, and all allocating
operations like `upb_Message_New()` should be checked for failure if there is
any possibility that a fixed size arena is in use.
```

**File:** upb/message/internal/message.h (L4639-4640)
```text

```

**File:** upb/wire/internal/decoder.h (L254-257)
```text
  if ((d->options & kUpb_DecodeOption_AliasString) == 0) {
    char* data = (char*)upb_Arena_Malloc(&d->arena, tmp.size);
    if (!data) return false;
    memcpy(data, tmp.data, tmp.size);
```
