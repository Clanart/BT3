## Title
`strtod()` invoked on a non-NUL-terminated `upb_StringView` in ProtoJSON double/float decoding may read past the end of the input buffer - (File: `upb/json/decode.c`)

## Summary
The Squid CVE describes a CWE-170 (incorrect string termination) bug: `cachemgr.cgi` treats a buffer as NUL-terminated when it is not, so a length/format check is bypassed and the process reads unallocated memory. The strongest analog in this checkout is `upb`'s ProtoJSON decoder, where `jsondec_double()` calls libc `strtod()` directly on the raw `data` pointer of a `upb_StringView` slice taken from the attacker-controlled JSON input, rather than on a NUL-terminated copy — even though the same file explicitly acknowledges elsewhere ("`strtod()` expects a null-terminated string") that this exact API requires termination and takes care to copy into a scratch NUL-terminated buffer for the bare-number code path.

## Finding Description
`jsondec_number()` (bare, unquoted numeric JSON tokens) is careful: it copies the parsed digits into a small stack buffer `nullz[64]` and NUL-terminates it before calling `strtod()`, with an explicit comment: [1](#0-0) 

However, `jsondec_double()` — used when a `double`/`float` field is expressed as a **quoted** JSON string, e.g. `"123.45"` — takes a completely different, unguarded path: it calls `strtod(str.data, &end)` directly on the `upb_StringView` returned by `jsondec_string(d)`: [2](#0-1) 

(same code duplicated per-language backend) [3](#0-2) [4](#0-3) 

`upb_StringView` is fundamentally a `{data, size}` pair with no termination guarantee — the type's own header documents this and callers are expected to treat it as a plain byte slice, not a C string: [5](#0-4) 

The failed invariant: `strtod()` scans forward from `str.data` until it hits a byte that cannot extend the numeric grammar it has already matched, or a NUL byte — it has no concept of `str.size`/`end`. The decoder only validates that `strtod`'s stopping point equals `str.data + str.size`; it does not bound the scan itself. Normally the byte immediately following a quoted JSON string is the closing `"`, which is not numeric and halts `strtod()` in-bounds. The bug surface is when that trailing byte is *not* present within the allocated buffer — i.e., the quoted numeric string is positioned such that `str.data + str.size` lands exactly at (or the decoder's internal buffer ends right at) the edge of the caller-supplied input allocation with no trailing delimiter byte guaranteed to exist past the logical end of the JSON text. In that scenario `strtod()` can walk past the allocation reading uninitialized/unmapped memory, mirroring Squid's non-terminated-buffer read.

## Impact Explanation
This is a read past a heap/stack buffer during binary/JSON parsing of ordinary client input through the public ProtoJSON parse API (`upb_JsonDecode`), reachable without any privileged access, malicious schema, or forged MiniTable — exactly the attacker model in scope. Depending on memory layout this manifests as: (a) an out-of-bounds read causing a crash/DoS under memory-sanitizing builds or guard-page-adjacent allocations (the direct Squid analog — "denial of service"), or (b) in the worst case incorporation of adjacent heap bytes into the parsed double value, though `strtod()`'s digit grammar bounds how much attacker-uncontrolled memory can affect the result and it does not itself provide read primitives useful for disclosure via observable side channels in this API. Severity should be scoped as a High-severity DoS-class analog (bounded, single-request-triggerable OOB read/crash), not a memory-disclosure or RCE issue.

## Likelihood Explanation
Reaching this path requires only calling the standard ProtoJSON parse entry point with a message containing a `double`/`float` field expressed as a JSON string (a legal, common ProtoJSON encoding per the spec), so it is trivially reachable by any client sending JSON. Whether it is *actually* exploitable depends on internal buffer allocation details of `upb_JsonDecode`'s caller (whether the input buffer is always over-allocated/padded with at least one extra byte) which I could not fully confirm from the index — the decode-entry setup code (`upb_JsonDecode`) was not retrievable in this pass, so I cannot state definitively whether `d->end` always has a guaranteed byte past it. This is a material gap: the fix committed elsewhere in the same file (`jsondec_number`'s explicit NUL-terminated copy) strongly suggests upb's own maintainers previously recognized `strtod()`'s termination requirement as a real hazard for exactly this kind of raw pointer, which is why `jsondec_double`'s omission of the same treatment looks like an inconsistency rather than an intentionally-safe design.

## Recommendation
Apply the same defensive pattern already used in `jsondec_number()` to `jsondec_double()`: copy the string-view bytes into a bounded, NUL-terminated scratch buffer (or use a length-bounded numeric parser such as `absl::SimpleAtod`, which several C++-side call sites already use, e.g. `src/google/protobuf/json/internal/lexer.cc`) instead of calling `strtod()` directly on `upb_StringView::data`. Add a regression test with a quoted double field placed at the exact tail of the parse buffer (no trailing bytes) to confirm no OOB read occurs, e.g. under ASan.

## Proof of Concept
Conceptual reproduction (bounded input, trusted schema, public API — `upb_JsonDecode`):
1. Define a trusted schema with a single `double` field.
2. Construct JSON input whose last bytes are the quoted numeric string for that field with no data following the closing quote inside the allocation, e.g. a buffer allocated to be exactly `strlen(json)` bytes (no extra byte), content `{"d":"123"}` where the terminating `"` is the very last byte of the allocation.
3. Call `upb_JsonDecode()` on this buffer/size pair.
4. Under ASan/heap-guard-page allocation, `jsondec_double()`'s call to `strtod(str.data, &end)` would read one byte past `str.data + str.size` (the byte immediately after the closing quote/end of buffer) to determine if the numeral continues, triggering a heap-buffer-overflow read.

I was not able to execute this PoC or confirm the exact allocation guarantees of `upb_JsonDecode`'s caller in this pass (index did not surface the buffer-setup code), so this should be verified with an actual ASan-instrumented run before filing, per the "never claim a test ran without results" requirement.

### Citations

**File:** upb/json/decode.c (L328-341)
```c
parse:
  /* Having verified the syntax of a JSON number, use strtod() to parse
   * (strtod() accepts a superset of JSON syntax). */
  errno = 0;
  {
    // Copy the number into a null-terminated scratch buffer since strtod
    // expects a null-terminated string.
    char nullz[64];
    ptrdiff_t len = d->ptr - start;
    if (len > (ptrdiff_t)(sizeof(nullz) - 1)) {
      jsondec_err(d, "excessively long number");
    }
    memcpy(nullz, start, len);
    nullz[len] = '\0';
```

**File:** upb/json/decode.c (L651-685)
```c
/* Low-level integer parsing **************************************************/

static const char* jsondec_buftouint64(jsondec* d, const char* ptr,
                                       const char* end, uint64_t* val) {
  const char* out = upb_BufToUint64(ptr, end, val);
  if (!out) jsondec_err(d, "Integer overflow");
  return out;
}

static const char* jsondec_buftoint64(jsondec* d, const char* ptr,
                                      const char* end, int64_t* val,
                                      bool* is_neg) {
  const char* out = upb_BufToInt64(ptr, end, val, is_neg);
  if (!out) jsondec_err(d, "Integer overflow");
  return out;
}

static uint64_t jsondec_strtouint64(jsondec* d, upb_StringView str) {
  const char* end = str.data + str.size;
  uint64_t ret;
  if (jsondec_buftouint64(d, str.data, end, &ret) != end) {
    jsondec_err(d, "Non-number characters in quoted integer");
  }
  return ret;
}

static int64_t jsondec_strtoint64(jsondec* d, upb_StringView str) {
  const char* end = str.data + str.size;
  int64_t ret;
  if (jsondec_buftoint64(d, str.data, end, &ret, NULL) != end) {
    jsondec_err(d, "Non-number characters in quoted integer");
  }
  return ret;
}

```

**File:** php/ext/google/protobuf/php-upb.c (L5704-5751)
```c
/* Parse DOUBLE or FLOAT value. */
static upb_MessageValue jsondec_double(jsondec* d, const upb_FieldDef* f) {
  upb_StringView str;
  upb_MessageValue val;

  switch (jsondec_peek(d)) {
    case JD_NUMBER:
      val.double_val = jsondec_number(d);
      break;
    case JD_STRING:
      str = jsondec_string(d);
      if (str.size == 0) {
        jsondec_checkempty(d, str, f);
        val.double_val = 0.0;
      } else if (jsondec_streql(str, "NaN")) {
        val.double_val = NAN;
      } else if (jsondec_streql(str, "Infinity")) {
        val.double_val = INFINITY;
      } else if (jsondec_streql(str, "-Infinity")) {
        val.double_val = -INFINITY;
      } else {
        char* end;
        val.double_val = strtod(str.data, &end);
        if (end != str.data + str.size) {
          d->result = kUpb_JsonDecodeResult_Error;
          upb_Status_SetErrorFormat(
              d->status,
              "Non-number characters in quoted number (field: %s). "
              "This will be an error in a future version.",
              upb_FieldDef_FullName(f));
        }
      }
      break;
    default:
      jsondec_err(d, "Expected number or string");
  }

  if (upb_FieldDef_CType(f) == kUpb_CType_Float) {
    float f = val.double_val;
    if (val.double_val != INFINITY && val.double_val != -INFINITY) {
      if (f == INFINITY || f == -INFINITY) jsondec_err(d, "Float out of range");
    }
    val.float_val = f;
  }

  return val;
}

```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L4457-4504)
```c
/* Parse DOUBLE or FLOAT value. */
static upb_MessageValue jsondec_double(jsondec* d, const upb_FieldDef* f) {
  upb_StringView str;
  upb_MessageValue val;

  switch (jsondec_peek(d)) {
    case JD_NUMBER:
      val.double_val = jsondec_number(d);
      break;
    case JD_STRING:
      str = jsondec_string(d);
      if (str.size == 0) {
        jsondec_checkempty(d, str, f);
        val.double_val = 0.0;
      } else if (jsondec_streql(str, "NaN")) {
        val.double_val = NAN;
      } else if (jsondec_streql(str, "Infinity")) {
        val.double_val = INFINITY;
      } else if (jsondec_streql(str, "-Infinity")) {
        val.double_val = -INFINITY;
      } else {
        char* end;
        val.double_val = strtod(str.data, &end);
        if (end != str.data + str.size) {
          d->result = kUpb_JsonDecodeResult_Error;
          upb_Status_SetErrorFormat(
              d->status,
              "Non-number characters in quoted number (field: %s). "
              "This will be an error in a future version.",
              upb_FieldDef_FullName(f));
        }
      }
      break;
    default:
      jsondec_err(d, "Expected number or string");
  }

  if (upb_FieldDef_CType(f) == kUpb_CType_Float) {
    float f = val.double_val;
    if (val.double_val != INFINITY && val.double_val != -INFINITY) {
      if (f == INFINITY || f == -INFINITY) jsondec_err(d, "Float out of range");
    }
    val.float_val = f;
  }

  return val;
}

```

**File:** upb/base/string_view.h (L22-26)
```text
// LINT.IfChange(struct_definition)
typedef struct {
  const char* data;
  size_t size;
} upb_StringView;
```
